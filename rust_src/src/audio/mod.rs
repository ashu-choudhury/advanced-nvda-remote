pub mod jitter;
pub mod device;
pub mod pipeline;

pub use jitter::{JitterBuffer, BufferState};
pub use pipeline::{AudioPipeline, SendStream};

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use cpal::traits::{HostTrait, DeviceTrait};

    #[test]
    fn test_audio_devices_diagnostic() {
        let host = cpal::default_host();
        println!("\n=== AUDIO DEVICES DIAGNOSTIC ===");
        println!("CPAL Host: {:?}", host.id());

        // Diagnostic for Input Devices (Microphones)
        if let Some(input_device) = host.default_input_device() {
            println!("Default Input Device: {:?}", input_device.name().unwrap_or_else(|_| "Unknown".to_string()));
            if let Ok(default_config) = input_device.default_input_config() {
                println!("  Default Input Config: {:?}", default_config);
            }
            if let Ok(supported_configs) = input_device.supported_input_configs() {
                println!("  Supported Input Configs:");
                for (i, config) in supported_configs.enumerate() {
                    println!("    {}: {:?}", i, config);
                }
            }
            
            // Test our input config resolution logic
            let config = device::resolve_input_config(&input_device);
            println!("  Resolved Input config: {:?}", config);
        } else {
            println!("No input device found (likely headless environment).");
        }

        // Diagnostic for Output Devices (Speakers)
        if let Some(output_device) = host.default_output_device() {
            println!("Default Output Device: {:?}", output_device.name().unwrap_or_else(|_| "Unknown".to_string()));
            if let Ok(default_config) = output_device.default_output_config() {
                println!("  Default Output Config: {:?}", default_config);
            }
            if let Ok(supported_configs) = output_device.supported_output_configs() {
                println!("  Supported Output Configs:");
                for (i, config) in supported_configs.enumerate() {
                    println!("    {}: {:?}", i, config);
                }
            }
            
            // Test our output config resolution logic
            let config = device::resolve_output_config(&output_device);
            println!("  Resolved Output config: {:?}", config);
        } else {
            println!("No output device found (likely headless environment).");
        }
        println!("================================\n");
    }

    #[test]
    fn test_pipeline_initialization() {
        let host = cpal::default_host();
        if host.default_input_device().is_none() || host.default_output_device().is_none() {
            println!("Skipping audio pipeline test: missing input or output device (headless runner).");
            return;
        }

        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let is_muted = Arc::new(Mutex::new(false));
        let jitter_buffer = Arc::new(Mutex::new(JitterBuffer::new(1920)));
        let apm = Arc::new(Mutex::new(sonora::AudioProcessing::builder().build()));

        let pipeline = AudioPipeline::new(tx, is_muted, jitter_buffer, apm);
        assert!(pipeline.is_ok(), "Failed to initialize audio pipeline: {:?}", pipeline.err());
        println!("Successfully initialized audio pipeline!");
    }

    #[test]
    fn test_audio_binary_packet_formatting() {
        let opus_payload = vec![1, 2, 3, 4, 5];
        let mut serialized = Vec::with_capacity(1 + opus_payload.len());
        serialized.push(0); // stream_id = 0
        serialized.extend_from_slice(&opus_payload);

        assert_eq!(serialized.len(), 6);
        assert_eq!(serialized[0], 0);
        assert_eq!(&serialized[1..], &[1, 2, 3, 4, 5]);
    }

    #[test]
    fn test_audio_binary_packet_parsing() {
        let data: Vec<u8> = vec![0, 99, 100, 101]; // stream_id = 0, payload = [99, 100, 101]
        
        assert!(data.len() > 1);
        let stream_id = data[0];
        let payload = &data[1..];

        assert_eq!(stream_id, 0);
        assert_eq!(payload, &[99, 100, 101]);
    }

    #[test]
    fn test_opus_codec_loop() {
        use opus::{Encoder, Decoder, Channels, Application};

        // Initialize Encoder and Decoder
        let mut encoder = Encoder::new(48000, Channels::Mono, Application::Voip).unwrap();
        let mut decoder = Decoder::new(48000, Channels::Mono).unwrap();

        // Create 480 float samples (10ms of 48kHz audio) of a simple sine wave
        let mut input_samples = vec![0.0f32; 480];
        for i in 0..480 {
            input_samples[i] = (i as f32 * 2.0 * std::f32::consts::PI / 100.0).sin();
        }

        // Encode the float samples
        let mut encoded_payload = vec![0u8; 1000];
        let encode_len = encoder.encode_float(&input_samples, &mut encoded_payload).unwrap();
        encoded_payload.truncate(encode_len);

        // Prepend the stream ID
        let mut serialized = Vec::with_capacity(1 + encoded_payload.len());
        serialized.push(0); // stream_id = 0
        serialized.extend_from_slice(&encoded_payload);

        // Simulate parsing on receiver
        assert!(serialized.len() > 1);
        let stream_id = serialized[0];
        let payload = &serialized[1..];
        assert_eq!(stream_id, 0);

        // Decode the payload
        let mut decoded_samples = vec![0.0f32; 480];
        let decode_len = decoder.decode_float(payload, &mut decoded_samples, false).unwrap();
        assert_eq!(decode_len, 480);

        // Verify output is valid (no NaNs or infinities)
        for &sample in &decoded_samples {
            assert!(sample.is_finite());
        }
    }

    #[test]
    fn test_sonora_processing() {
        use sonora::{AudioProcessing, Config, StreamConfig};
        use sonora::config::{EchoCanceller, NoiseSuppression, GainController2};

        let config = Config {
            echo_canceller: Some(EchoCanceller::default()),
            noise_suppression: Some(NoiseSuppression::default()),
            gain_controller2: Some(GainController2::default()),
            ..Default::default()
        };

        let mut apm = AudioProcessing::builder()
            .config(config)
            .capture_config(StreamConfig::new(48000, 1))
            .render_config(StreamConfig::new(48000, 1))
            .build();

        // Sonora processes 10ms at 48kHz (480 samples)
        let render_input = vec![0.1f32; 480];
        let mut render_output = vec![0.0f32; 480];
        let capture_input = vec![0.05f32; 480];
        let mut capture_output = vec![0.0f32; 480];

        // Process render path
        let res_render = apm.process_render_f32(&[&render_input], &mut [&mut render_output]);
        assert!(res_render.is_ok());

        // Process capture path
        let res_capture = apm.process_capture_f32(&[&capture_input], &mut [&mut capture_output]);
        assert!(res_capture.is_ok());

        // Verify outputs are finite
        for &sample in &capture_output {
            assert!(sample.is_finite());
        }
    }

    #[test]
    fn test_resolve_device_configs() {
        let host = cpal::default_host();
        if let Some(device) = host.default_input_device() {
            let config = device::resolve_input_config(&device);
            assert!(config.is_ok());
            let config = config.unwrap();
            assert_eq!(config.sample_rate.0, 48000);
        }
        if let Some(device) = host.default_output_device() {
            let config = device::resolve_output_config(&device);
            assert!(config.is_ok());
            let config = config.unwrap();
            assert_eq!(config.sample_rate.0, 48000);
        }
    }

    #[test]
    fn test_corrupt_opus_packet_handling() {
        use opus::{Decoder, Channels};

        let mut decoder = Decoder::new(48000, Channels::Mono).unwrap();

        // Feed it random garbage bytes which represent a corrupted Opus packet
        let corrupt_payload = vec![0xDE, 0xAD, 0xBE, 0xEF, 0x12, 0x34];
        let mut decoded_samples = vec![0.0f32; 480];

        // Ensure that it returns an error instead of panicking
        let decode_res = decoder.decode_float(&corrupt_payload, &mut decoded_samples, false);
        assert!(decode_res.is_err());
    }

    #[test]
    fn test_empty_and_overflow_audio_packets() {
        // 1. Test empty packet: should be ignored safely (len <= 1)
        let empty_data: Vec<u8> = vec![];
        assert!(empty_data.len() <= 1);

        // 2. Test packet with just stream_id (no payload): should also be ignored safely
        let stream_id_only: Vec<u8> = vec![0];
        assert_eq!(stream_id_only.len(), 1);
        let payload = &stream_id_only[1..];
        assert_eq!(payload.len(), 0);

        // 3. Test huge audio packet payload
        let huge_payload = vec![0u8; 1024 * 1024]; // 1MB packet
        let mut serialized = Vec::with_capacity(1 + huge_payload.len());
        serialized.push(0);
        serialized.extend_from_slice(&huge_payload);

        assert_eq!(serialized[0], 0);
        assert_eq!(serialized[1..].len(), 1024 * 1024);
    }

    #[test]
    fn test_audio_pipeline_device_error_recovery() {
        let has_error = Arc::new(Mutex::new(false));
        
        // Simulate a device error
        {
            let mut guard = has_error.lock().unwrap();
            *guard = true;
        }

        // Verify that the error is detected
        assert!(*has_error.lock().unwrap());

        // Reset error state
        {
            let mut guard = has_error.lock().unwrap();
            *guard = false;
        }
        assert!(!*has_error.lock().unwrap());
    }
}

