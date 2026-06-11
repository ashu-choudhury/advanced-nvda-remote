use std::sync::{Arc, Mutex};
use std::collections::VecDeque;
use cpal::traits::{DeviceTrait, StreamTrait};
use opus::{Encoder, Channels, Application};
use sonora::AudioProcessing;
use tokio::sync::mpsc::UnboundedSender;
use crate::audio::jitter::JitterBuffer;

// We no longer use a structured AudioPacket struct; data is serialized as a raw binary packet: [stream_id, ...payload]

// Thread-safe wrapper for cpal::Stream (specifically WASAPI raw COM pointers on Windows)
pub struct SendStream(pub cpal::Stream);
unsafe impl Send for SendStream {}
unsafe impl Sync for SendStream {}

#[allow(dead_code)]
pub struct AudioPipeline {
    pub input_stream: Option<SendStream>,
    pub output_stream: Option<SendStream>,
    pub loopback_stream: Option<SendStream>,
    pub is_muted: Arc<Mutex<bool>>,
    pub jitter_buffer: Arc<Mutex<JitterBuffer>>,
    pub apm: Arc<Mutex<AudioProcessing>>,
    pub has_error: Arc<Mutex<bool>>,
    pub input_device_name: String,
    pub output_device_name: String,
}

impl AudioPipeline {
    pub fn new(
        tx: UnboundedSender<Vec<u8>>,
        is_muted: Arc<Mutex<bool>>,
        jitter_buffer: Arc<Mutex<JitterBuffer>>,
        apm: Arc<Mutex<AudioProcessing>>,
    ) -> Result<Self, String> {
        let (input_device, output_device) = super::device::get_devices()?;
        let input_device_name = input_device.name().unwrap_or_else(|_| "Unknown".to_string());
        let output_device_name = output_device.name().unwrap_or_else(|_| "Unknown".to_string());
        let input_stream_config = super::device::resolve_input_config(&input_device)?;
        let output_stream_config = super::device::resolve_output_config(&output_device)?;

        // Opus encoder setup (Mono, 48kHz, Voip mode)
        let mut encoder = Encoder::new(48000, Channels::Mono, Application::Voip)
            .map_err(|e| format!("Failed to create Opus encoder: {:?}", e))?;
        encoder.set_bitrate(opus::Bitrate::Bits(32000)) // 32kbps is clear for speech
            .map_err(|e| format!("Failed to set Opus bitrate: {:?}", e))?;

        let has_error = Arc::new(Mutex::new(false));
        let render_buffer = Arc::new(Mutex::new(VecDeque::new()));

        // Loopback stream setup (WASAPI loopback)
        let loopback_config = output_stream_config.clone();
        let loopback_channels = loopback_config.channels as usize;
        let render_buffer_loopback = Arc::clone(&render_buffer);
        let has_error_loopback = Arc::clone(&has_error);

        let loopback_stream = output_device.build_input_stream(
            &loopback_config,
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                if let Ok(mut rb) = render_buffer_loopback.lock() {
                    if loopback_channels == 1 {
                        rb.extend(data);
                    } else {
                        let mut i = 0;
                        while i < data.len() {
                            let mut sum = 0.0;
                            for _ in 0..loopback_channels {
                                if i < data.len() {
                                    sum += data[i];
                                    i += 1;
                                }
                            }
                            rb.push_back(sum / (loopback_channels as f32));
                        }
                    }
                }
            },
            move |err| {
                eprintln!("Audio loopback stream error: {:?}", err);
                if let Ok(mut guard) = has_error_loopback.lock() {
                    *guard = true;
                }
            },
            None
        ).map_err(|e| format!("Failed to build CPAL loopback stream: {:?}", e))?;

        // Microphone capture stream
        let mut capture_buffer = Vec::new();
        let apm_capture = Arc::clone(&apm);
        let is_muted_capture = Arc::clone(&is_muted);
        let num_input_channels = input_stream_config.channels as usize;
        let render_buffer_capture = Arc::clone(&render_buffer);
        let has_error_capture = Arc::clone(&has_error);

        let input_stream = input_device.build_input_stream(
            &input_stream_config,
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                // If microphone is muted, skip capture entirely
                if *is_muted_capture.lock().unwrap() {
                    return;
                }

                // Downmix input channels to Mono
                if num_input_channels == 1 {
                    capture_buffer.extend_from_slice(data);
                } else {
                    let mut i = 0;
                    while i < data.len() {
                        let mut sum = 0.0;
                        for _ in 0..num_input_channels {
                            if i < data.len() {
                                sum += data[i];
                                i += 1;
                            }
                        }
                        capture_buffer.push(sum / (num_input_channels as f32));
                    }
                }

                // Sonora processes exactly 10ms (480 samples at 48kHz) chunks
                while capture_buffer.len() >= 480 {
                    let chunk: Vec<f32> = capture_buffer.drain(..480).collect();
                    let mut processed = vec![0.0f32; 480];

                    // Retrieve 480 samples from loopback render buffer
                    let mut render_chunk = vec![0.0f32; 480];
                    if let Ok(mut rb) = render_buffer_capture.lock() {
                        let available = rb.len();
                        if available >= 480 {
                            for sample in render_chunk.iter_mut() {
                                *sample = rb.pop_front().unwrap_or(0.0);
                            }
                        } else {
                            for i in 0..available {
                                render_chunk[i] = rb.pop_front().unwrap_or(0.0);
                            }
                        }
                    }

                    {
                        let mut apm_lock = apm_capture.lock().unwrap();
                        let mut render_out = render_chunk.clone();
                        // Feed render chunk first
                        let _ = apm_lock.process_render_f32(&[&render_chunk], &mut [&mut render_out]);
                        // Feed capture chunk next
                        let _ = apm_lock.process_capture_f32(&[&chunk], &mut [&mut processed]);
                    }

                    // Compress frame with Opus
                    let mut opus_payload = vec![0u8; 1000];
                    if let Ok(len) = encoder.encode_float(&processed, &mut opus_payload) {
                        opus_payload.truncate(len);

                        // Prepend 1-byte stream_id (0 = Microphone) to raw Opus payload
                        let mut serialized = Vec::with_capacity(1 + opus_payload.len());
                        serialized.push(0);
                        serialized.extend_from_slice(&opus_payload);
                        let _ = tx.send(serialized);
                    }
                }
            },
            move |err| {
                eprintln!("Audio input stream error: {:?}", err);
                if let Ok(mut guard) = has_error_capture.lock() {
                    *guard = true;
                }
            },
            None
        ).map_err(|e| format!("Failed to build CPAL input stream: {:?}", e))?;

        // Playback stream
        let jb_playback = Arc::clone(&jitter_buffer);
        let output_channels = output_stream_config.channels as usize;
        let has_error_output = Arc::clone(&has_error);
        let output_stream = output_device.build_output_stream(
            &output_stream_config,
            move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let mut jb = jb_playback.lock().unwrap();
                if output_channels == 1 {
                    jb.pop(data);
                } else {
                    let mut mono_buffer = vec![0.0f32; data.len() / output_channels];
                    jb.pop(&mut mono_buffer);
                    let mut write_idx = 0;
                    for &sample in &mono_buffer {
                        for _ in 0..output_channels {
                            if write_idx < data.len() {
                                data[write_idx] = sample;
                                write_idx += 1;
                            }
                        }
                    }
                }
            },
            move |err| {
                eprintln!("Audio output stream error: {:?}", err);
                if let Ok(mut guard) = has_error_output.lock() {
                    *guard = true;
                }
            },
            None
        ).map_err(|e| format!("Failed to build CPAL output stream: {:?}", e))?;

        // Start streams
        input_stream.play().map_err(|e| format!("Failed to start input stream: {:?}", e))?;
        output_stream.play().map_err(|e| format!("Failed to start output stream: {:?}", e))?;
        loopback_stream.play().map_err(|e| format!("Failed to start loopback stream: {:?}", e))?;

        Ok(Self {
            input_stream: Some(SendStream(input_stream)),
            output_stream: Some(SendStream(output_stream)),
            loopback_stream: Some(SendStream(loopback_stream)),
            is_muted,
            jitter_buffer,
            apm,
            has_error,
            input_device_name,
            output_device_name,
        })
    }
}
