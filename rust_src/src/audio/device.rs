use cpal::traits::{DeviceTrait, HostTrait};

pub fn get_devices() -> Result<(cpal::Device, cpal::Device), String> {
    let host = cpal::default_host();
    let input_device = host.default_input_device()
        .ok_or_else(|| "Failed to find default input device (microphone)".to_string())?;
    let output_device = host.default_output_device()
        .ok_or_else(|| "Failed to find default output device (speaker)".to_string())?;
    Ok((input_device, output_device))
}

pub fn resolve_input_config(device: &cpal::Device) -> Result<cpal::StreamConfig, String> {
    let default_config = device.default_input_config()
        .map_err(|e| format!("Failed to get default input config: {:?}", e))?;
    let mut target_sample_rate = cpal::SampleRate(48000);
    let mut target_channels = default_config.channels();

    if let Ok(supported_configs) = device.supported_input_configs() {
        for config in supported_configs {
            if config.min_sample_rate().0 <= 48000 && 48000 <= config.max_sample_rate().0 {
                target_sample_rate = cpal::SampleRate(48000);
                target_channels = config.channels();
                break;
            }
        }
    }

    Ok(cpal::StreamConfig {
        channels: target_channels,
        sample_rate: target_sample_rate,
        buffer_size: cpal::BufferSize::Default,
    })
}

pub fn resolve_output_config(device: &cpal::Device) -> Result<cpal::StreamConfig, String> {
    let default_config = device.default_output_config()
        .map_err(|e| format!("Failed to get default output config: {:?}", e))?;
    let mut target_sample_rate = cpal::SampleRate(48000);
    let mut target_channels = default_config.channels();

    if let Ok(supported_configs) = device.supported_output_configs() {
        for config in supported_configs {
            if config.min_sample_rate().0 <= 48000 && 48000 <= config.max_sample_rate().0 {
                target_sample_rate = cpal::SampleRate(48000);
                target_channels = config.channels();
                break;
            }
        }
    }

    Ok(cpal::StreamConfig {
        channels: target_channels,
        sample_rate: target_sample_rate,
        buffer_size: cpal::BufferSize::Default,
    })
}
