pub mod audio;
pub mod webrtc;

use pyo3::prelude::*;

#[pyfunction]
fn init_leader(stun_servers: Vec<String>) -> PyResult<()> {
    webrtc::init_leader(stun_servers).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn init_follower(stun_servers: Vec<String>) -> PyResult<()> {
    webrtc::init_follower(stun_servers).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn create_offer() -> PyResult<String> {
    webrtc::create_offer().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn set_offer(offer_json: String) -> PyResult<()> {
    webrtc::set_offer(offer_json).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn create_answer() -> PyResult<String> {
    webrtc::create_answer().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn set_answer(answer_json: String) -> PyResult<()> {
    webrtc::set_answer(answer_json).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn add_ice_candidate(candidate_json: String) -> PyResult<()> {
    webrtc::add_ice_candidate(candidate_json).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn get_local_candidates() -> PyResult<Vec<String>> {
    webrtc::get_local_candidates().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn send_message(msg: String) -> PyResult<bool> {
    webrtc::send_message(msg).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn recv_message() -> PyResult<Option<String>> {
    webrtc::recv_message().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn is_connected() -> PyResult<bool> {
    webrtc::is_connected().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn start_audio() -> PyResult<()> {
    webrtc::start_audio().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn stop_audio() -> PyResult<()> {
    webrtc::stop_audio().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn set_mic_muted(muted: bool) -> PyResult<()> {
    webrtc::set_mic_muted(muted).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn is_mic_muted() -> PyResult<bool> {
    webrtc::is_mic_muted().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn is_audio_active() -> PyResult<bool> {
    webrtc::is_audio_active().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn send_file_message(msg: String) -> PyResult<bool> {
    webrtc::send_file_message(msg).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn recv_file_message() -> PyResult<Option<String>> {
    webrtc::recv_file_message().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn has_file_channel() -> PyResult<bool> {
    webrtc::has_file_channel().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
}

#[pyfunction]
fn close() -> PyResult<()> {
    webrtc::close().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
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
    m.add_function(wrap_pyfunction!(start_audio, m)?)?;
    m.add_function(wrap_pyfunction!(stop_audio, m)?)?;
    m.add_function(wrap_pyfunction!(set_mic_muted, m)?)?;
    m.add_function(wrap_pyfunction!(is_mic_muted, m)?)?;
    m.add_function(wrap_pyfunction!(is_audio_active, m)?)?;
    m.add_function(wrap_pyfunction!(send_file_message, m)?)?;
    m.add_function(wrap_pyfunction!(recv_file_message, m)?)?;
    m.add_function(wrap_pyfunction!(has_file_channel, m)?)?;
    m.add_function(wrap_pyfunction!(close, m)?)?;
    Ok(())
}
