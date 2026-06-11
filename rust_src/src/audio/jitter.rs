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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jitter_buffer_initial_state() {
        let jb = JitterBuffer::new(10);
        assert_eq!(jb.state, BufferState::Buffering);
        assert_eq!(jb.buffer.len(), 0);
    }

    #[test]
    fn test_jitter_buffer_buffering_silence() {
        let mut jb = JitterBuffer::new(10);
        let mut out = [1.0; 5];
        jb.pop(&mut out);
        // Pop should return all zeros because state is Buffering and we haven't reached target
        assert!(out.iter().all(|&x| x == 0.0));
        assert_eq!(jb.state, BufferState::Buffering);
    }

    #[test]
    fn test_jitter_buffer_transitions_to_playing() {
        let mut jb = JitterBuffer::new(10);
        
        // Push 6 samples - still buffering
        jb.push(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]);
        assert_eq!(jb.state, BufferState::Buffering);

        // Push 4 more samples to reach target_samples = 10
        jb.push(&[7.0, 8.0, 9.0, 10.0]);
        assert_eq!(jb.state, BufferState::Playing);

        // Pop 5 samples - should return the samples we pushed
        let mut out = [0.0; 5];
        jb.pop(&mut out);
        assert_eq!(out, [1.0, 2.0, 3.0, 4.0, 5.0]);
        assert_eq!(jb.state, BufferState::Playing);
    }

    #[test]
    fn test_jitter_buffer_capping() {
        let mut jb = JitterBuffer::new(5); // max is 10
        
        // Push 15 samples (exceeds max_samples = 10)
        jb.push(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0]);
        
        // The buffer should be capped to 10 samples, discarding the oldest 5
        assert_eq!(jb.buffer.len(), 10);
        
        // Oldest discarded should be 1..5. Remaining should be 6..15.
        let mut out = [0.0; 5];
        jb.pop(&mut out);
        assert_eq!(out, [6.0, 7.0, 8.0, 9.0, 10.0]);
    }

    #[test]
    fn test_jitter_buffer_underrun() {
        let mut jb = JitterBuffer::new(5);
        jb.push(&[1.0, 2.0, 3.0, 4.0, 5.0]); // Transitions to Playing
        
        let mut out = [0.0; 8];
        jb.pop(&mut out);
        
        // Pops available 5 samples, fills the rest 3 with silence, and transitions to Buffering
        assert_eq!(out[0..5], [1.0, 2.0, 3.0, 4.0, 5.0]);
        assert_eq!(out[5..8], [0.0, 0.0, 0.0]);
        assert_eq!(jb.state, BufferState::Buffering);
    }

    #[test]
    fn test_jitter_buffer_high_frequency_burst() {
        let mut jb = JitterBuffer::new(480); // 10ms target, max is 960 samples
        
        // Push 100 packets of 480 samples each (total 48,000 samples)
        let chunk = [0.1f32; 480];
        for _ in 0..100 {
            jb.push(&chunk);
        }

        // Ensure that the buffer is strictly capped at max_samples (960)
        assert_eq!(jb.buffer.len(), 960);

        // Pop all samples and verify they are all 0.1f32 (not silenced or corrupt)
        let mut out = vec![0.0f32; 960];
        jb.pop(&mut out);
        assert!(out.iter().all(|&x| x == 0.1));
        
        // State should be Playing since we had enough samples
        assert_eq!(jb.state, BufferState::Playing);
    }

    #[test]
    fn test_jitter_buffer_jittery_network() {
        let mut jb = JitterBuffer::new(480);
        
        // 1. Initial push - transitions to Playing
        let chunk = [0.2f32; 480];
        jb.push(&chunk);
        assert_eq!(jb.state, BufferState::Playing);

        // 2. Pop half - works fine
        let mut out = [0.0f32; 240];
        jb.pop(&mut out);
        assert!(out.iter().all(|&x| x == 0.2));

        // 3. Network lag: try to pop 480 samples (exceeds available 240) -> causes underrun
        let mut out_large = [0.0f32; 480];
        jb.pop(&mut out_large);
        
        // First 240 samples should be 0.2, remaining 240 should be silence (0.0)
        assert!(out_large[0..240].iter().all(|&x| x == 0.2));
        assert!(out_large[240..480].iter().all(|&x| x == 0.0));
        // State must transition back to Buffering
        assert_eq!(jb.state, BufferState::Buffering);

        // 4. Packet burst: network recovers and pushes 3 packets at once
        jb.push(&[0.3f32; 480]);
        jb.push(&[0.4f32; 480]);
        jb.push(&[0.5f32; 480]);

        // Capped at max_samples = 960. Oldest samples (0.3) are discarded.
        // It should contain 0.4 and 0.5.
        assert_eq!(jb.buffer.len(), 960);
        let mut out_play = [0.0f32; 960];
        jb.pop(&mut out_play);
        assert!(out_play[0..480].iter().all(|&x| x == 0.4));
        assert!(out_play[480..960].iter().all(|&x| x == 0.5));
    }
}
