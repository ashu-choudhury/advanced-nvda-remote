use std::collections::VecDeque;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum BufferState {
    Buffering,
    Playing,
}

pub struct JitterBuffer {
    buffer: VecDeque<f32>,
    state: BufferState,
    target_samples: usize, // e.g. 40ms * 48000Hz = 1920 samples
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

        // Cap maximum buffer size to prevent latency drift
        let max_samples = self.target_samples * 2;
        if self.buffer.len() > max_samples {
            let excess = self.buffer.len() - max_samples;
            self.buffer.drain(..excess);
        }

        if self.state == BufferState::Buffering && self.buffer.len() >= self.target_samples {
            self.state = BufferState::Playing;
        }
    }

    pub fn pop(&mut self, out: &mut [f32]) {
        let to_read = out.len();

        if self.state == BufferState::Buffering {
            if self.buffer.len() >= self.target_samples {
                self.state = BufferState::Playing;
            } else {
                // Return silence while buffering
                for sample in out.iter_mut() {
                    *sample = 0.0;
                }
                return;
            }
        }

        if self.buffer.len() < to_read {
            // Buffer underrun: pop whatever we have and fill the rest with silence
            let available = self.buffer.len();
            for i in 0..available {
                out[i] = self.buffer.pop_front().unwrap_or(0.0);
            }
            for i in available..to_read {
                out[i] = 0.0;
            }
            self.state = BufferState::Buffering;
        } else {
            for sample in out.iter_mut() {
                *sample = self.buffer.pop_front().unwrap_or(0.0);
            }
        }
    }
}
