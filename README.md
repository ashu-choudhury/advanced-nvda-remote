# Advanced NVDA Remote

**Advanced NVDA Remote** is a high-performance, feature-rich upgrade for the standard NVDA Remote access tool. By establishing direct peer-to-peer (P2P) connections via WebRTC, it delivers a remote control experience that is significantly faster, more responsive, and packed with extra capabilities like integrated voice chat and high-speed file transfer.

---

## The Latency Problem & The P2P Solution

### The Bottleneck
Standard NVDA Remote routes all keyboard inputs, speech text, and braille outputs through a central relay server. When these servers are far from the users (e.g., across oceans), it introduces a baseline latency of **~200ms each way**, resulting in a **~400ms round-trip delay** for every action. This latency makes remote navigation feel sluggish, laggy, and unresponsive.

### The Direct Boost
**Advanced NVDA Remote** bypasses the relay server after the initial signaling phase. By establishing direct **WebRTC Peer-to-Peer Data Channels** between the controller and controlled machines, latency is slashed down to **5ms to 50ms** (or even <1ms on LAN). This direct boost makes remote control feel as if you are sitting right in front of the target machine.

---

## Features

### 1. WebRTC P2P Data Channels
* **Near-Zero Latency:** High-speed keypress, speech, and braille synchronization.
* **Automatic Relay Fallback:** If symmetric firewalls or strict NAT configurations prevent a direct P2P connection, the add-on silently and seamlessly falls back to standard relay routing. You get maximum speed when possible and 100% connection reliability.

### 2. High-Fidelity Voice Communication
* **Integrated Voice Chat:** Speak with your peer directly through the remote session without needing external apps like Zoom or Discord.
* **Acoustics Processing Engine:** Utilizes the **Sonora** library for Echo Cancellation (AEC), Noise Suppression (NS), and Automatic Gain Control (AGC) to ensure crystal-clear audio.
* **Opus Compression:** Uses the state-of-the-art Opus codec for low-bandwidth, high-quality audio compression.
* **Rust Audio Backend:** Built using a native Rust engine with CPAL for reliable audio capture and playback.
* **Dynamic Audio Swapping:** Automatically detects system default audio input/output device changes and layout differences, swapping devices on the fly without interrupting your stream.

### 3. Optimized High-Speed File Transfer
* **Dedicated File Channel:** Uses WebRTC data channel 2 for binary file transfers, keeping control input completely lag-free.
* **Zlib Compression:** Compresses file streams in-transit for rapid delivery.
* **MTU-Safe Chunking:** Uses optimized 32KB chunks to prevent WebRTC MTU frame drops or packet loss on unstable networks.
* **Flow Control with ACKs:** Implements a sliding window protocol (1MB maximum outstanding) with disk-write acknowledgments from the receiver. This prevents memory leaks, CPU spikes, and avoids blocking NVDA's main UI thread during large transfers (even up to multi-gigabyte files).
* **Cancel Support:** Instantly abort transfers from either the sending or receiving side.

---

## Keyboard Shortcuts

The add-on registers the following global keyboard shortcuts for P2P remote sessions:

| Shortcut | Action | Description |
|---|---|---|
| `Control + Shift + M` | Toggle Microphone | Mutes or unmutes your microphone during a voice session. |
| `Control + Shift + C` | Cancel File Transfer | Instantly aborts any active inbound or outbound file transfer. |
| `Alt + NVDA + P` | Ping Peer | Measures the round-trip latency (RTT) between peers and speaks/displays the result. |

---

## Installation & Usage

### Installation
1. Download the latest packaged add-on: `advanced-nvda-remote.nvda-addon`.
2. Double-click or open the file to install the add-on in NVDA.
3. Restart NVDA when prompted.

### Usage
1. Connect exactly as you would with standard NVDA Remote:
   * Go to **NVDA Menu (NVDA+N) -> Tools -> Remote -> Connect**.
   * Choose your role (Host or Client), enter the connection key, and press Connect.
2. The add-on will automatically perform WebRTC signaling behind the scenes.
3. Once connected, NVDA will announce if P2P was established successfully. The audio pipeline and shortcuts will become active automatically.

---

## Development & Building

If you want to build the add-on from source:

### Prerequisites
* [Rust Toolchain](https://rustup.rs/) (Cargo)
* Python 3.10+ (installed on Windows)

### Build Commands
Run the unified build automation script `build.py` at the root directory:

* **Build for Host Architecture** (fastest, recommended for local dev):
  ```powershell
  python build.py
  ```
* **Build for All Architectures** (compiles x64, x86, and ARM64 native binaries into the package):
  ```powershell
  python build.py --all
  ```
* **Build for a Specific Target**:
  ```powershell
  python build.py --target <target-triple>
  ```

---

## License

This project is licensed under the GNU General Public License (GPL) version 2, matching the license of NVDA. See the [LICENSE](LICENSE) file for details.

