use std::sync::{Arc, Mutex};
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
use pyo3::prelude::*;

static RUNTIME: Lazy<Runtime> = Lazy::new(|| {
    Runtime::new().expect("Failed to create Tokio runtime")
});

struct PeerState {
    peer_connection: Arc<RTCPeerConnection>,
    data_channel: Option<Arc<RTCDataChannel>>,
    local_candidates: Arc<Mutex<Vec<String>>>,
    received_messages: Arc<Mutex<VecDeque<String>>>,
    is_connected: Arc<Mutex<bool>>,
}

static STATE: Lazy<Mutex<Option<PeerState>>> = Lazy::new(|| Mutex::new(None));

#[pyfunction]
fn init_leader(stun_servers: Vec<String>) -> PyResult<()> {
    let mut state_guard = STATE.lock().unwrap();
    if state_guard.is_some() {
        return Ok(());
    }

    let local_candidates = Arc::new(Mutex::new(Vec::new()));
    let received_messages = Arc::new(Mutex::new(VecDeque::new()));
    let is_connected = Arc::new(Mutex::new(false));

    let local_candidates_clone = Arc::clone(&local_candidates);
    let received_messages_clone = Arc::clone(&received_messages);
    let is_connected_clone = Arc::clone(&is_connected);

    let (peer_connection, data_channel) = RUNTIME.block_on(async move {
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

        let pc = Arc::new(api.new_peer_connection(config).await.expect("Failed to create PeerConnection"));

        // Set up ICE candidate callback
        let lc_clone = Arc::clone(&local_candidates_clone);
        pc.on_ice_candidate(Box::new(move |c| {
            if let Some(candidate) = c {
                if let Ok(candidate_json) = serde_json::to_string(&candidate.to_json().unwrap()) {
                    let mut lc = lc_clone.lock().unwrap();
                    lc.push(candidate_json);
                }
            }
            Box::pin(async {})
        }));

        // Leader creates the data channel
        let dc = pc.create_data_channel("nvda-remote", None).await.expect("Failed to create DataChannel");
        let dc_shared = Arc::clone(&dc);

        let ic_clone = Arc::clone(&is_connected_clone);
        dc.on_open(Box::new(move || {
            let mut ic = ic_clone.lock().unwrap();
            *ic = true;
            Box::pin(async {})
        }));

        let ic_clone2 = Arc::clone(&is_connected_clone);
        dc.on_close(Box::new(move || {
            let mut ic = ic_clone2.lock().unwrap();
            *ic = false;
            Box::pin(async {})
        }));

        let rm_clone = Arc::clone(&received_messages_clone);
        dc.on_message(Box::new(move |msg| {
            if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                let mut rm = rm_clone.lock().unwrap();
                rm.push_back(msg_str);
            }
            Box::pin(async {})
        }));

        (pc, Some(dc_shared))
    });

    *state_guard = Some(PeerState {
        peer_connection,
        data_channel,
        local_candidates,
        received_messages,
        is_connected,
    });

    Ok(())
}

#[pyfunction]
fn init_follower(stun_servers: Vec<String>) -> PyResult<()> {
    let mut state_guard = STATE.lock().unwrap();
    if state_guard.is_some() {
        return Ok(());
    }

    let local_candidates = Arc::new(Mutex::new(Vec::new()));
    let received_messages = Arc::new(Mutex::new(VecDeque::new()));
    let is_connected = Arc::new(Mutex::new(false));

    let local_candidates_clone = Arc::clone(&local_candidates);
    let received_messages_clone = Arc::clone(&received_messages);
    let is_connected_clone = Arc::clone(&is_connected);

    // Follower listens for data channel
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

        let pc = Arc::new(api.new_peer_connection(config).await.expect("Failed to create PeerConnection"));

        // Set up ICE candidate callback
        let lc_clone = Arc::clone(&local_candidates_clone);
        pc.on_ice_candidate(Box::new(move |c| {
            if let Some(candidate) = c {
                if let Ok(candidate_json) = serde_json::to_string(&candidate.to_json().unwrap()) {
                    let mut lc = lc_clone.lock().unwrap();
                    lc.push(candidate_json);
                }
            }
            Box::pin(async {})
        }));

        // Follower waits for on_data_channel
        let ic_clone = Arc::clone(&is_connected_clone);
        let rm_clone = Arc::clone(&received_messages_clone);
        pc.on_data_channel(Box::new(move |dc| {
            let ic_open = Arc::clone(&ic_clone);
            dc.on_open(Box::new(move || {
                let mut ic = ic_open.lock().unwrap();
                *ic = true;
                Box::pin(async {})
            }));

            let ic_close = Arc::clone(&ic_clone);
            dc.on_close(Box::new(move || {
                let mut ic = ic_close.lock().unwrap();
                *ic = false;
                Box::pin(async {})
            }));

            let rm_msg = Arc::clone(&rm_clone);
            dc.on_message(Box::new(move |msg| {
                if let Ok(msg_str) = String::from_utf8(msg.data.to_vec()) {
                    let mut rm = rm_msg.lock().unwrap();
                    rm.push_back(msg_str);
                }
                Box::pin(async {})
            }));

            // Store the data channel
            let mut state_guard = STATE.lock().unwrap();
            if let Some(ref mut state) = *state_guard {
                state.data_channel = Some(dc);
            }

            Box::pin(async {})
        }));

        pc
    });

    *state_guard = Some(PeerState {
        peer_connection: pc,
        data_channel: None,
        local_candidates,
        received_messages,
        is_connected,
    });

    Ok(())
}

#[pyfunction]
fn create_offer() -> PyResult<String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let offer = RUNTIME.block_on(async move {
            let offer = pc.create_offer(None).await.expect("Failed to create offer");
            pc.set_local_description(offer.clone()).await.expect("Failed to set local description");
            offer
        });
        let sdp_json = serde_json::to_string(&offer).unwrap();
        Ok(sdp_json)
    } else {
        Err(pyo3::exceptions::PyRuntimeError::new_err("PeerConnection not initialized"))
    }
}

#[pyfunction]
fn set_offer(offer_json: String) -> PyResult<()> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let offer: RTCSessionDescription = serde_json::from_str(&offer_json).unwrap();
        RUNTIME.block_on(async move {
            pc.set_remote_description(offer).await.expect("Failed to set remote description");
        });
        Ok(())
    } else {
        Err(pyo3::exceptions::PyRuntimeError::new_err("PeerConnection not initialized"))
    }
}

#[pyfunction]
fn create_answer() -> PyResult<String> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let answer = RUNTIME.block_on(async move {
            let answer = pc.create_answer(None).await.expect("Failed to create answer");
            pc.set_local_description(answer.clone()).await.expect("Failed to set local description");
            answer
        });
        let sdp_json = serde_json::to_string(&answer).unwrap();
        Ok(sdp_json)
    } else {
        Err(pyo3::exceptions::PyRuntimeError::new_err("PeerConnection not initialized"))
    }
}

#[pyfunction]
fn set_answer(answer_json: String) -> PyResult<()> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let answer: RTCSessionDescription = serde_json::from_str(&answer_json).unwrap();
        RUNTIME.block_on(async move {
            pc.set_remote_description(answer).await.expect("Failed to set remote description");
        });
        Ok(())
    } else {
        Err(pyo3::exceptions::PyRuntimeError::new_err("PeerConnection not initialized"))
    }
}

#[pyfunction]
fn add_ice_candidate(candidate_json: String) -> PyResult<()> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let pc = Arc::clone(&state.peer_connection);
        let candidate: RTCIceCandidateInit = serde_json::from_str(&candidate_json).unwrap();
        RUNTIME.block_on(async move {
            pc.add_ice_candidate(candidate).await.expect("Failed to add remote ICE candidate");
        });
        Ok(())
    } else {
        Err(pyo3::exceptions::PyRuntimeError::new_err("PeerConnection not initialized"))
    }
}

#[pyfunction]
fn get_local_candidates() -> PyResult<Vec<String>> {
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

#[pyfunction]
fn send_message(msg: String) -> PyResult<bool> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        if let Some(ref dc) = state.data_channel {
            let dc_clone = Arc::clone(dc);
            let success = RUNTIME.block_on(async move {
                dc_clone.send_text(msg).await.is_ok()
            });
            Ok(success)
        } else {
            Ok(false)
        }
    } else {
        Ok(false)
    }
}

#[pyfunction]
fn recv_message() -> PyResult<Option<String>> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let mut rm = state.received_messages.lock().unwrap();
        Ok(rm.pop_front())
    } else {
        Ok(None)
    }
}

#[pyfunction]
fn is_connected() -> PyResult<bool> {
    let state_guard = STATE.lock().unwrap();
    if let Some(ref state) = *state_guard {
        let ic = state.is_connected.lock().unwrap();
        Ok(*ic)
    } else {
        Ok(false)
    }
}

#[pyfunction]
fn close() -> PyResult<()> {
    let mut state_guard = STATE.lock().unwrap();
    if let Some(state) = state_guard.take() {
        let pc = state.peer_connection;
        RUNTIME.block_on(async move {
            let _ = pc.close().await;
        });
    }
    Ok(())
}

#[pymodule]
fn p2p_webrtc(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(init_leader, m)?)?;
    m.add_function(wrap_pyfunction!(init_follower, m)?)?;
    m.add_function(wrap_pyfunction!(create_offer, m)?)?;
    m.add_function(wrap_pyfunction!(set_offer, m)?)?;
    m.add_function(wrap_pyfunction!(create_answer, m)?)?;
    m.add_function(wrap_pyfunction!(set_answer, m)?)?;
    m.add_function(wrap_pyfunction!(add_ice_candidate, m)?)?;
    m.add_function(wrap_pyfunction!(get_local_candidates, m)?)?;
    m.add_function(wrap_pyfunction!(send_message, m)?)?;
    m.add_function(wrap_pyfunction!(recv_message, m)?)?;
    m.add_function(wrap_pyfunction!(is_connected, m)?)?;
    m.add_function(wrap_pyfunction!(close, m)?)?;
    Ok(())
}
