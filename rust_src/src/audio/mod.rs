pub mod jitter;
pub mod device;
pub mod pipeline;

pub use jitter::{JitterBuffer, BufferState};
pub use pipeline::{AudioPipeline, AudioPacket, SendStream};

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
}
