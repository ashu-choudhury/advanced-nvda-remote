use std::sync::{Arc, Mutex, Condvar};
use std::collections::VecDeque;
use once_cell::sync::Lazy;
use tokio::runtime::Runtime;
use webrtc::api::APIBuilder;
use webrtc::api::media_engine::MediaEngine;
use webrtc::peer_connection::configuration::RTCConfiguration;
use webrtc::peer_connection::RTCPeerConnection;
use webrtc::ice_transport::ice_server::RTCIceServer;
use webrtc::ice_transport::ice_candidate::RTCIceCandidateInit;
use webrtc::data_channel::RTCDataChannel;
use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
use bytes::Bytes;
use crate::audio::{AudioPipeline, JitterBuffer};

pub static RUNTIME: Lazy<Runtime> = Lazy::new(|| {
    Runtime::new().expect("Failed to create Tokio runtime")
});

#[allow(dead_code)]
struct PeerState {
    peer_connection: Arc<RTCPeerConnection>,
    data_channel: Option<Arc<RTCDataChannel>>,
    audio_channel: Option<Arc<RTCDataChannel>>,
    file_channel: Option<Arc<RTCDataChannel>>,
    local_candidates: Arc<Mutex<Vec<String>>>,
    received_messages: Arc<Mutex<VecDeque<String>>>,
    received_file_messages: Arc<Mutex<VecDeque<String>>>,
    is_connected: Arc<Mutex<bool>>,
    received_condvar: Arc<Condvar>,
}

#[allow(dead_code)]
struct AudioState {
    pipeline: AudioPipeline,
    audio_tx: tokio::sync::mpsc::UnboundedSender<Vec<u8>>,
    jitter_buffer: Arc<Mutex<JitterBuffer>>,
    apm: Arc<Mutex<sonora::AudioProcessing>>,
    is_muted: Arc<Mutex<bool>>,
    decoder: Arc<Mutex<opus::Decoder>>,
}

static AUDIO_STATE: Lazy<Mutex<Option<AudioState>>> = Lazy::new(|| Mutex::new(None));
static STATE: Lazy<Mutex<Option<PeerState>>> = Lazy::new(|| Mutex::new(None));

pub fn init_leader(stun_servers: Vec<String>) -> Result<(), String> {
    let mut state_guard = STATE.lock().unwrap();
    if state_guard.is_some() {
        return Ok(());
    }

    let local_candidates = Arc::new(Mutex::new(Vec::new()));
    let received_messages = Arc::new(Mutex::new(VecDeque::new()));
    let received_file_messages = Arc::new(Mutex::new(VecDeque::new()));
    let is_connected = Arc::new(Mutex::new(false));
    let received_condvar = Arc::new(Condvar::new());
 
    let local_candidates_clone = Arc::clone(&local_candidates);
    let received_messages_clone = Arc::clone(&received_messages);
    let received_file_messages_clone = Arc::clone(&received_file_messages);
    let is_connected_clone = Arc::clone(&is_connected);
    let received_condvar_clone = Arc::clone(&received_condvar);
 
    let (peer_connection, data_channel, audio_channel, file_channel) = RUNTIME.block_on(async move {
        let m = MediaEngine::default();
        let api = APIBuilder::new()
            .with_media_engine(m)
            .build();
 
        let mut ice_servers = Vec::new();
        for server in stun_servers {
            ice_servers.push(RTCIceServer {
                urls: vec![server],
                ..Default::default()
            });
        }
        let config = RTCConfiguration {
            ice_servers,
            ..Default::default()
        };
 
        let pc = Arc::new(
            api.new_peer_connection(config)
                .await
                .map_err(|e| format!("Failed to create PeerConnection: {:?}", e))?,
        );
 
        let lc_clone = Arc::clone(&local_candidates_clone);
        pc.on_ice_candidate(Box::new(move |c| {
            if let Some(candidate) = c {
                if let Ok(json_val) = candidate.to_json() {
                    if let Ok(candidate_json) = serde_json::to_string(&json_val) {
                        let mut lc = lc_clone.lock().unwrap();
                        lc.push(candidate_json);
                    }
                }
            }
            Box::pin(async {})
        }));
 
        let dc = pc
            .create_data_channel("nvda-remote", None)
            .await
            .map_err(|e| format!("Failed to create DataChannel: {:?}", e))?;
        let dc_shared = Arc::clone(&dc);
 
        let ic_clone = Arc::clone(&is_connected_clone);
        let cv_clone1 = Arc::clone(&received_condvar_clone);
        dc.on_open(Box::new(move || {
            let mut ic = ic_clone.lock().unwrap();
            *ic = true;
            cv_clone1.notify_all();
            Box::pin(async {})
        }));
 
        let ic_clone2 = Arc::clone(&is_connected_clone);
        let cv_clone2 = Arc::clone(&received_condvar_clone);
        dc.on_close(Box::new(move || {
            let mut ic = ic_clone2.lock().unwrap();
            *ic = false;
            cv_clone2.notify_all();
            Box::pin(async {})
        }));
 
        let rm_clone = Arc::clone(&received_messages_clone);
        let cv_clone3 = Arc::clone(&received_condvar_clone);
        dc.on_message(Box::new(move |msg| {
            if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                let mut rm = rm_clone.lock().unwrap();
                rm.push_back(msg_str);
                cv_clone3.notify_one();
            }
            Box::pin(async {})
        }));
 
        // Leader creates the audio data channel
        let audio_dc = pc
            .create_data_channel("nvda-remote-audio", None)
            .await
            .map_err(|e| format!("Failed to create Audio Data Channel: {:?}", e))?;
        let audio_dc_shared = Arc::clone(&audio_dc);
 
        audio_dc.on_message(Box::new(move |msg| {
            let data = msg.data.clone();
            let audio_state_guard = AUDIO_STATE.lock().unwrap();
            if let Some(ref audio_state) = *audio_state_guard {
                let decoder = Arc::clone(&audio_state.decoder);
                let jb = Arc::clone(&audio_state.jitter_buffer);
                // Decode synchronously in the WebRTC receive callback for maximum latency reduction and zero jitter
                if data.len() > 1 {
                    let stream_id = data[0];
                    let payload = &data[1..];
                    if stream_id == 0 {
                        let mut dec = decoder.lock().unwrap();
                        let mut decoded = vec![0.0f32; 480];
                        if let Ok(len) = dec.decode_float(payload, &mut decoded, false) {
                            decoded.truncate(len);
                            let mut jb_lock = jb.lock().unwrap();
                            jb_lock.push(&decoded);
                        }
                    }
                }
            }
            Box::pin(async {})
        }));
 
        // Leader creates the file data channel
        let file_dc = pc
            .create_data_channel("nvda-remote-file", None)
            .await
            .map_err(|e| format!("Failed to create File Data Channel: {:?}", e))?;
        let file_dc_shared = Arc::clone(&file_dc);
 
        let rfm_clone = Arc::clone(&received_file_messages_clone);
        file_dc.on_message(Box::new(move |msg| {
            if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                let mut rfm = rfm_clone.lock().unwrap();
                rfm.push_back(msg_str);
            }
            Box::pin(async {})
        }));
 
        Ok::<(Arc<RTCPeerConnection>, Option<Arc<RTCDataChannel>>, Option<Arc<RTCDataChannel>>, Option<Arc<RTCDataChannel>>), String>((
            pc,
            Some(dc_shared),
            Some(audio_dc_shared),
            Some(file_dc_shared),
        ))
    })?;
 
    *state_guard = Some(PeerState {
        peer_connection,
        data_channel,
        audio_channel,
        file_channel,
        local_candidates,
        received_messages,
        received_file_messages,
        is_connected,
        received_condvar,
    });

    Ok(())
}

pub fn init_follower(stun_servers: Vec<String>) -> Result<(), String> {
    let mut state_guard = STATE.lock().unwrap();
    if state_guard.is_some() {
        return Ok(());
    }

    let local_candidates = Arc::new(Mutex::new(Vec::new()));
    let received_messages = Arc::new(Mutex::new(VecDeque::new()));
    let received_file_messages = Arc::new(Mutex::new(VecDeque::new()));
    let is_connected = Arc::new(Mutex::new(false));
    let received_condvar = Arc::new(Condvar::new());

    let local_candidates_clone = Arc::clone(&local_candidates);
    let received_messages_clone = Arc::clone(&received_messages);
    let received_file_messages_clone = Arc::clone(&received_file_messages);
    let is_connected_clone = Arc::clone(&is_connected);
    let received_condvar_clone = Arc::clone(&received_condvar);

    let pc = RUNTIME.block_on(async move {
        let m = MediaEngine::default();
        let api = APIBuilder::new()
            .with_media_engine(m)
            .build();

        let mut ice_servers = Vec::new();
        for server in stun_servers {
            ice_servers.push(RTCIceServer {
                urls: vec![server],
                ..Default::default()
            });
        }
        let config = RTCConfiguration {
            ice_servers,
            ..Default::default()
        };

        let pc = Arc::new(
            api.new_peer_connection(config)
                .await
                .map_err(|e| format!("Failed to create PeerConnection: {:?}", e))?,
        );

        let lc_clone = Arc::clone(&local_candidates_clone);
        pc.on_ice_candidate(Box::new(move |c| {
            if let Some(candidate) = c {
                if let Ok(json_val) = candidate.to_json() {
                    if let Ok(candidate_json) = serde_json::to_string(&json_val) {
                        let mut lc = lc_clone.lock().unwrap();
                        lc.push(candidate_json);
                    }
                }
            }
            Box::pin(async {})
        }));

        let ic_clone = Arc::clone(&is_connected_clone);
        let rm_clone = Arc::clone(&received_messages_clone);
        let cv_clone = Arc::clone(&received_condvar_clone);
        pc.on_data_channel(Box::new(move |dc| {
            let label = dc.label().to_string();
            let ic_open = Arc::clone(&ic_clone);
            let ic_close = Arc::clone(&ic_clone);
            let rm_msg = Arc::clone(&rm_clone);
            let cv_open = Arc::clone(&cv_clone);
            let cv_close = Arc::clone(&cv_clone);
            let cv_msg = Arc::clone(&cv_clone);

            if label == "nvda-remote-audio" {
                let audio_dc = Arc::clone(&dc);
                audio_dc.on_message(Box::new(move |msg| {
                    let data = msg.data.clone();
                    let audio_state_guard = AUDIO_STATE.lock().unwrap();
                    if let Some(ref audio_state) = *audio_state_guard {
                        let decoder = Arc::clone(&audio_state.decoder);
                        let jb = Arc::clone(&audio_state.jitter_buffer);
                        // Decode synchronously in the WebRTC receive callback for maximum latency reduction and zero jitter
                        if data.len() > 1 {
                            let stream_id = data[0];
                            let payload = &data[1..];
                            if stream_id == 0 {
                                let mut dec = decoder.lock().unwrap();
                                let mut decoded = vec![0.0f32; 480];
                                if let Ok(len) = dec.decode_float(payload, &mut decoded, false) {
                                    decoded.truncate(len);
                                    let mut jb_lock = jb.lock().unwrap();
                                    jb_lock.push(&decoded);
                                }
                            }
                        }
                    }
                    Box::pin(async {})
                }));

                let mut state_guard = STATE.lock().unwrap();
                if let Some(ref mut state) = *state_guard {
                    state.audio_channel = Some(audio_dc);
                }
            } else if label == "nvda-remote-file" {
                let file_dc = Arc::clone(&dc);
                let rfm_clone = Arc::clone(&received_file_messages_clone);
                file_dc.on_message(Box::new(move |msg| {
                    if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                        let mut rfm = rfm_clone.lock().unwrap();
                        rfm.push_back(msg_str);
                    }
                    Box::pin(async {})
                }));

                let mut state_guard = STATE.lock().unwrap();
                if let Some(ref mut state) = *state_guard {
                    state.file_channel = Some(file_dc);
                }
            } else {
                dc.on_open(Box::new(move || {
                    let mut ic = ic_open.lock().unwrap();
                    *ic = true;
                    cv_open.notify_all();
                    Box::pin(async {})
                }));

                dc.on_close(Box::new(move || {
                    let mut ic = ic_close.lock().unwrap();
                    *ic = false;
                    cv_close.notify_all();
                    Box::pin(async {})
                }));

                dc.on_message(Box::new(move |msg| {
                    if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                        let mut rm = rm_msg.lock().unwrap();
                        rm.push_back(msg_str);
                        cv_msg.notify_one();
                    }
                    Box::pin(async {})
                }));

                // Store the data channel
                let mut state_guard = STATE.lock().unwrap();
                if let Some(ref mut state) = *state_guard {
                    state.data_channel = Some(dc);
                }
            }

            Box::pin(async {})
        }));

        Ok::<Arc<RTCPeerConnection>, String>(pc)
    })?;

    *state_guard = Some(PeerState {
        peer_connection: pc,
        data_channel: None,
        audio_channel: None,
        file_channel: None,
        local_candidates,
        received_messages,
        received_file_messages,
        is_connected,
        received_condvar,
    });

    Ok(())
}

pub fn create_offer() -> Result<String, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let offer = RUNTIME.block_on(async move {
            let offer = pc.create_offer(None).await
                .map_err(|e| format!("Failed to create offer: {:?}", e))?;
            pc.set_local_description(offer.clone()).await
                .map_err(|e| format!("Failed to set local description: {:?}", e))?;
            Ok::<RTCSessionDescription, String>(offer)
        })?;
        let sdp_json = serde_json::to_string(&offer)
            .map_err(|e| format!("Failed to serialize offer: {:?}", e))?;
        Ok(sdp_json)
    } else {
        Err("PeerConnection not initialized".to_string())
    }
}

pub fn set_offer(offer_json: String) -> Result<(), String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let offer: RTCSessionDescription = serde_json::from_str(&offer_json)
            .map_err(|e| format!("Failed to parse offer JSON: {:?}", e))?;
        RUNTIME.block_on(async move {
            pc.set_remote_description(offer).await
                .map_err(|e| format!("Failed to set remote description: {:?}", e))
        })?;
        Ok(())
    } else {
        Err("PeerConnection not initialized".to_string())
    }
}

pub fn create_answer() -> Result<String, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let answer = RUNTIME.block_on(async move {
            let answer = pc.create_answer(None).await
                .map_err(|e| format!("Failed to create answer: {:?}", e))?;
            pc.set_local_description(answer.clone()).await
                .map_err(|e| format!("Failed to set local description: {:?}", e))?;
            Ok::<RTCSessionDescription, String>(answer)
        })?;
        let sdp_json = serde_json::to_string(&answer)
            .map_err(|e| format!("Failed to serialize answer: {:?}", e))?;
        Ok(sdp_json)
    } else {
        Err("PeerConnection not initialized".to_string())
    }
}

pub fn set_answer(answer_json: String) -> Result<(), String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let answer: RTCSessionDescription = serde_json::from_str(&answer_json)
            .map_err(|e| format!("Failed to parse answer JSON: {:?}", e))?;
        RUNTIME.block_on(async move {
            pc.set_remote_description(answer).await
                .map_err(|e| format!("Failed to set remote description: {:?}", e))
        })?;
        Ok(())
    } else {
        Err("PeerConnection not initialized".to_string())
    }
}

pub fn add_ice_candidate(candidate_json: String) -> Result<(), String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let candidate: RTCIceCandidateInit = serde_json::from_str(&candidate_json)
            .map_err(|e| format!("Failed to parse ICE candidate JSON: {:?}", e))?;
        RUNTIME.block_on(async move {
            pc.add_ice_candidate(candidate).await
                .map_err(|e| format!("Failed to add remote ICE candidate: {:?}", e))
        })?;
        Ok(())
    } else {
        Err("PeerConnection not initialized".to_string())
    }
}

pub fn get_local_candidates() -> Result<Vec<String>, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let mut lc = state.local_candidates.lock().unwrap();
        let candidates = lc.clone();
        lc.clear();
        Ok(candidates)
    } else {
        Ok(Vec::new())
    }
}

pub fn send_message(msg: String) -> Result<bool, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        if let Some(ref dc) = state.data_channel {
            let dc_clone = Arc::clone(dc);
            RUNTIME.spawn(async move {
                let _ = dc_clone.send_text(msg).await;
            });
            Ok(true)
        } else {
            Ok(false)
        }
    } else {
        Ok(false)
    }
}

pub fn recv_message() -> Result<Option<String>, String> {
    let (received_messages, is_connected, received_condvar) = {
        let state_guard = STATE.lock().unwrap();
        if let Some(ref state) = *state_guard {
            (
                Arc::clone(&state.received_messages),
                Arc::clone(&state.is_connected),
                Arc::clone(&state.received_condvar),
            )
        } else {
            return Ok(None);
        }
    }; // state_guard is dropped here!

    let mut rm = received_messages.lock().unwrap();
    while rm.is_empty() && *is_connected.lock().unwrap() {
        rm = received_condvar.wait(rm).unwrap();
    }
    Ok(rm.pop_front())
}

pub fn is_connected() -> Result<bool, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let ic = state.is_connected.lock().unwrap();
        Ok(*ic)
    } else {
        Ok(false)
    }
}

pub fn start_audio() -> Result<(), String> {
    let mut audio_state_guard = AUDIO_STATE.lock().unwrap();
    if audio_state_guard.is_some() {
        return Ok(());
    }

    let state_guard = STATE.lock().unwrap();
    let audio_channel = if let Some(ref state) = *state_guard {
        if let Some(ref ac) = state.audio_channel {
            Arc::clone(ac)
        } else {
            return Err("Audio Data Channel not available".to_string());
        }
    } else {
        return Err("PeerConnection not initialized".to_string());
    };

    // Voice session starts MUTED by default
    let is_muted = Arc::new(Mutex::new(true));
    let jitter_buffer = Arc::new(Mutex::new(JitterBuffer::new(1920))); // 40ms target

    // Sonora APM setup
    let config = sonora::Config {
        echo_canceller: Some(sonora::config::EchoCanceller::default()),
        noise_suppression: Some(sonora::config::NoiseSuppression::default()),
        gain_controller2: Some(sonora::config::GainController2::default()),
        ..Default::default()
    };
    let apm = sonora::AudioProcessing::builder()
        .config(config)
        .capture_config(sonora::StreamConfig::new(48000, 1))
        .render_config(sonora::StreamConfig::new(48000, 1))
        .build();
    let apm = Arc::new(Mutex::new(apm));

    let (audio_tx, mut audio_rx) = tokio::sync::mpsc::unbounded_channel::<Vec<u8>>();

    let pipeline = AudioPipeline::new(
        audio_tx.clone(),
        Arc::clone(&is_muted),
        Arc::clone(&jitter_buffer),
        Arc::clone(&apm),
    )?;

    let audio_channel_clone = Arc::clone(&audio_channel);
    RUNTIME.spawn(async move {
        while let Some(data) = audio_rx.recv().await {
            let _ = audio_channel_clone.send(&Bytes::from(data)).await;
        }
    });

    let decoder = Arc::new(Mutex::new(
        opus::Decoder::new(48000, opus::Channels::Mono)
            .map_err(|e| format!("Decoder init failed: {:?}", e))?
    ));

    *audio_state_guard = Some(AudioState {
        pipeline,
        audio_tx,
        jitter_buffer,
        apm,
        is_muted,
        decoder,
    });

    Ok(())
}

pub fn stop_audio() -> Result<(), String> {
    let mut audio_state_guard = AUDIO_STATE.lock().unwrap();
    *audio_state_guard = None;
    Ok(())
}

pub fn set_mic_muted(muted: bool) -> Result<(), String> {
    let audio_state_guard = AUDIO_STATE.lock().unwrap();
    if let Some(ref audio_state) = *audio_state_guard {
        let mut is_muted = audio_state.is_muted.lock().unwrap();
        *is_muted = muted;
    }
    Ok(())
}

pub fn is_mic_muted() -> Result<bool, String> {
    let audio_state_guard = AUDIO_STATE.lock().unwrap();
    if let Some(ref audio_state) = *audio_state_guard {
        let is_muted = audio_state.is_muted.lock().unwrap();
        Ok(*is_muted)
    } else {
        Ok(true) // Default to muted if audio not started
    }
}

pub fn is_audio_active() -> Result<bool, String> {
    let audio_state_guard = AUDIO_STATE.lock().unwrap();
    if let Some(ref audio_state) = *audio_state_guard {
        if let Ok(has_err) = audio_state.pipeline.has_error.lock() {
            Ok(!*has_err)
        } else {
            Ok(false)
        }
    } else {
        Ok(false)
    }
}

pub fn send_file_message(msg: String) -> Result<bool, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        if let Some(ref dc) = state.file_channel {
            let dc_clone = Arc::clone(dc);
            RUNTIME.spawn(async move {
                let _ = dc_clone.send_text(msg).await;
            });
            Ok(true)
        } else {
            Ok(false)
        }
    } else {
        Ok(false)
    }
}

pub fn recv_file_message() -> Result<Option<String>, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let mut rfm = state.received_file_messages.lock().unwrap();
        Ok(rfm.pop_front())
    } else {
        Ok(None)
    }
}

pub fn has_file_channel() -> Result<bool, String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        Ok(state.file_channel.is_some())
    } else {
        Ok(false)
    }
}

pub fn close() -> Result<(), String> {
    let mut audio_state_guard = AUDIO_STATE.lock().unwrap();
    *audio_state_guard = None;

    let mut state_guard = STATE.lock().unwrap();
    if let Some(state) = state_guard.take() {
        {
            let mut ic = state.is_connected.lock().unwrap();
            *ic = false;
        }
        state.received_condvar.notify_all();
        let pc = state.peer_connection;
        RUNTIME.block_on(async move {
            let _ = pc.close().await;
        });
    }
    Ok(())
}

pub fn check_default_devices_changed() -> Result<bool, String> {
    let audio_state_guard = AUDIO_STATE.lock().unwrap();
    if let Some(ref audio_state) = *audio_state_guard {
        use cpal::traits::{HostTrait, DeviceTrait};
        let host = cpal::default_host();
        
        let current_default_input = host.default_input_device();
        let current_default_output = host.default_output_device();
        
        let input_changed = if let Some(ref dev) = current_default_input {
            let name = dev.name().unwrap_or_else(|_| "Unknown".to_string());
            name != audio_state.pipeline.input_device_name
        } else {
            !audio_state.pipeline.input_device_name.is_empty() && audio_state.pipeline.input_device_name != "Unknown"
        };
        
        let output_changed = if let Some(ref dev) = current_default_output {
            let name = dev.name().unwrap_or_else(|_| "Unknown".to_string());
            name != audio_state.pipeline.output_device_name
        } else {
            !audio_state.pipeline.output_device_name.is_empty() && audio_state.pipeline.output_device_name != "Unknown"
        };
        
        Ok(input_changed || output_changed)
    } else {
        Ok(false)
    }
}


#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static TEST_MUTEX: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

    #[test]
    fn test_initial_webrtc_state() {
        let _guard = TEST_MUTEX.lock().unwrap();
        let _ = stop_audio();
        let _ = close();
        let connected = is_connected();
        assert!(connected.is_ok());
        assert_eq!(connected.unwrap(), false);

        let file_chan = has_file_channel();
        assert!(file_chan.is_ok());
        assert_eq!(file_chan.unwrap(), false);

        let active = is_audio_active();
        assert!(active.is_ok());
        assert_eq!(active.unwrap(), false);

        let muted = is_mic_muted();
        assert!(muted.is_ok());
        assert_eq!(muted.unwrap(), true);

        let changed = check_default_devices_changed();
        assert!(changed.is_ok());
        assert_eq!(changed.unwrap(), false);
    }

    #[test]
    fn test_messaging_before_init() {
        let _guard = TEST_MUTEX.lock().unwrap();
        let _ = stop_audio();
        let _ = close();
        let sent = send_message("hello".to_string());
        assert!(sent.is_ok());
        assert_eq!(sent.unwrap(), false);

        let sent_file = send_file_message("hello file".to_string());
        assert!(sent_file.is_ok());
        assert_eq!(sent_file.unwrap(), false);

        let recv = recv_message();
        assert!(recv.is_ok());
        assert_eq!(recv.unwrap(), None);

        let recv_file = recv_file_message();
        assert!(recv_file.is_ok());
        assert_eq!(recv_file.unwrap(), None);
    }

    #[test]
    fn test_default_devices_changed_detection() {
        let _guard = TEST_MUTEX.lock().unwrap();
        let _ = stop_audio();
        let _ = close();

        let is_muted = Arc::new(Mutex::new(false));
        let jitter_buffer = Arc::new(Mutex::new(JitterBuffer::new(1920)));
        let apm = Arc::new(Mutex::new(sonora::AudioProcessing::builder().build()));
        let has_error = Arc::new(Mutex::new(false));

        let mock_pipeline = AudioPipeline {
            input_stream: None,
            output_stream: None,
            loopback_stream: None,
            is_muted: Arc::clone(&is_muted),
            jitter_buffer: Arc::clone(&jitter_buffer),
            apm: Arc::clone(&apm),
            has_error: Arc::clone(&has_error),
            input_device_name: "Fictional Input Device".to_string(),
            output_device_name: "Fictional Output Device".to_string(),
        };

        let decoder = Arc::new(Mutex::new(
            opus::Decoder::new(48000, opus::Channels::Mono).unwrap()
        ));
        let (audio_tx, _) = tokio::sync::mpsc::unbounded_channel();

        {
            let mut audio_state_guard = AUDIO_STATE.lock().unwrap();
            *audio_state_guard = Some(AudioState {
                pipeline: mock_pipeline,
                audio_tx,
                jitter_buffer,
                apm,
                is_muted,
                decoder,
            });
        }

        let changed = check_default_devices_changed();
        assert!(changed.is_ok());
        assert_eq!(changed.unwrap(), true);

        use cpal::traits::{HostTrait, DeviceTrait};
        let host = cpal::default_host();
        let default_input_name = host.default_input_device()
            .map(|d| d.name().unwrap_or_else(|_| "Unknown".to_string()))
            .unwrap_or_else(|| "Unknown".to_string());
        let default_output_name = host.default_output_device()
            .map(|d| d.name().unwrap_or_else(|_| "Unknown".to_string()))
            .unwrap_or_else(|| "Unknown".to_string());

        {
            let mut audio_state_guard = AUDIO_STATE.lock().unwrap();
            if let Some(ref mut state) = *audio_state_guard {
                state.pipeline.input_device_name = default_input_name;
                state.pipeline.output_device_name = default_output_name;
            }
        }

        let changed = check_default_devices_changed();
        assert!(changed.is_ok());
        assert_eq!(changed.unwrap(), false);

        let _ = stop_audio();
    }
}

