use std::sync::{Arc, Mutex};
use std::collections::VecDeque;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use opus::{Encoder, Channels, Application};
use sonora::AudioProcessing;
use tokio::sync::mpsc::UnboundedSender;

// Audio packet wrapper for multi-stream support (e.g. mic, system sounds)
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct AudioPacket {
    pub stream_id: u8, // 0 = Microphone
    pub payload: Vec<u8>,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum BufferState {
    Buffering,
    Playing,
}

pub struct JitterBuffer {
    buffer: VecDeque<f32>,
    state: BufferState,
    target_samples: usize, // e.g. 60ms * 48000Hz = 2880 samples
}

impl JitterBuffer {
    pub fn new(target_samples: usize) -> Self {
        Self {
            buffer: VecDeque::new(),
            state: BufferState::Buffering,
            target_samples,
        }
    }

    pub fn push(&mut self, samples: &[f32]) {
        self.buffer.extend(samples);
        if self.state == BufferState::Buffering && self.buffer.len() >= self.target_samples {
            self.state = BufferState::Playing;
        }
    }

    pub fn pop(&mut self, out: &mut [f32]) {
        if self.state == BufferState::Buffering {
            // Silence while filling up buffer
            for sample in out.iter_mut() {
                *sample = 0.0;
            }
            return;
        }

        let to_read = out.len();
        if self.buffer.len() < to_read {
            // Buffer underrun: go back to buffering and play silence
            self.state = BufferState::Buffering;
            for sample in out.iter_mut() {
                *sample = 0.0;
            }
            return;
        }

        for sample in out.iter_mut() {
            *sample = self.buffer.pop_front().unwrap_or(0.0);
        }
    }
}

// Thread-safe wrapper for cpal::Stream (specifically WASAPI raw COM pointers on Windows)
pub struct SendStream(pub cpal::Stream);
unsafe impl Send for SendStream {}
unsafe impl Sync for SendStream {}

pub struct AudioPipeline {
    input_stream: SendStream,
    output_stream: SendStream,
    is_muted: Arc<Mutex<bool>>,
    jitter_buffer: Arc<Mutex<JitterBuffer>>,
    apm: Arc<Mutex<AudioProcessing>>,
}

impl AudioPipeline {
    pub fn new(
        tx: UnboundedSender<Vec<u8>>,
        is_muted: Arc<Mutex<bool>>,
        jitter_buffer: Arc<Mutex<JitterBuffer>>,
        apm: Arc<Mutex<AudioProcessing>>,
    ) -> Result<Self, String> {
        let host = cpal::default_host();
        let input_device = host.default_input_device()
            .ok_or_else(|| "Failed to find default input device (microphone)".to_string())?;
        let output_device = host.default_output_device()
            .ok_or_else(|| "Failed to find default output device (speaker)".to_string())?;

        // Query default config and search for 48kHz support for input device
        let default_input_config = input_device.default_input_config()
            .map_err(|e| format!("Failed to get default input config: {:?}", e))?;
        let mut target_input_sample_rate = cpal::SampleRate(48000);
        let mut target_input_channels = default_input_config.channels();

        if let Ok(supported_configs) = input_device.supported_input_configs() {
            for config in supported_configs {
                if config.min_sample_rate().0 <= 48000 && 48000 <= config.max_sample_rate().0 {
                    target_input_sample_rate = cpal::SampleRate(48000);
                    target_input_channels = config.channels();
                    break;
                }
            }
        }

        let input_stream_config = cpal::StreamConfig {
            channels: target_input_channels,
            sample_rate: target_input_sample_rate,
            buffer_size: cpal::BufferSize::Default,
        };

        // Query default config and search for 48kHz support for output device
        let default_output_config = output_device.default_output_config()
            .map_err(|e| format!("Failed to get default output config: {:?}", e))?;
        let mut target_output_sample_rate = cpal::SampleRate(48000);
        let mut target_output_channels = default_output_config.channels();

        if let Ok(supported_configs) = output_device.supported_output_configs() {
            for config in supported_configs {
                if config.min_sample_rate().0 <= 48000 && 48000 <= config.max_sample_rate().0 {
                    target_output_sample_rate = cpal::SampleRate(48000);
                    target_output_channels = config.channels();
                    break;
                }
            }
        }

        let output_stream_config = cpal::StreamConfig {
            channels: target_output_channels,
            sample_rate: target_output_sample_rate,
            buffer_size: cpal::BufferSize::Default,
        };

        // Opus encoder setup (Mono, 48kHz, Voip mode)
        let mut encoder = Encoder::new(48000, Channels::Mono, Application::Voip)
            .map_err(|e| format!("Failed to create Opus encoder: {:?}", e))?;
        encoder.set_bitrate(opus::Bitrate::Bits(32000)) // 32kbps is crystal clear for speech
            .map_err(|e| format!("Failed to set Opus bitrate: {:?}", e))?;

        // Microphone capture stream
        let mut capture_buffer = Vec::new();
        let apm_capture = Arc::clone(&apm);
        let is_muted_capture = Arc::clone(&is_muted);
        let num_input_channels = target_input_channels as usize;
        
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

                    {
                        let mut apm_lock = apm_capture.lock().unwrap();
                        let _ = apm_lock.process_capture_f32(&[&chunk], &mut [&mut processed]);
                    }

                    // Compress frame with Opus
                    let mut opus_payload = vec![0u8; 1000];
                    if let Ok(len) = encoder.encode_float(&processed, &mut opus_payload) {
                        opus_payload.truncate(len);
                        
                        // Wrap in stream structure
                        let packet = AudioPacket {
                            stream_id: 0,
                            payload: opus_payload,
                        };

                        if let Ok(serialized) = serde_json::to_vec(&packet) {
                            let _ = tx.send(serialized);
                        }
                    }
                }
            },
            |err| {
                eprintln!("Audio input stream error: {:?}", err);
            },
            None
        ).map_err(|e| format!("Failed to build CPAL input stream: {:?}", e))?;

        // Playback stream
        let jb_playback = Arc::clone(&jitter_buffer);
        let output_channels = target_output_channels as usize;
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
            |err| {
                eprintln!("Audio output stream error: {:?}", err);
            },
            None
        ).map_err(|e| format!("Failed to build CPAL output stream: {:?}", e))?;

        // Start streams immediately
        input_stream.play().map_err(|e| format!("Failed to start input stream: {:?}", e))?;
        output_stream.play().map_err(|e| format!("Failed to start output stream: {:?}", e))?;

        Ok(Self {
            input_stream: SendStream(input_stream),
            output_stream: SendStream(output_stream),
            is_muted,
            jitter_buffer,
            apm,
        })
    }
}
