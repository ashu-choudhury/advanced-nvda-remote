# Advanced NVDA Remote

**Advanced NVDA Remote** is a smart, high-performance upgrade for the standard NVDA Remote access tool. By establishing direct peer-to-peer (P2P) connections, it allows users to experience remote control that is significantly faster, more responsive, and more reliable.

---

## Why Use Advanced NVDA Remote?

Standard NVDA Remote routes all keyboard inputs, speech, and braille outputs through a central relay server (typically located in the US). For international users or those on slower connections, this introduces a latency of **150ms to 300ms**, causing remote navigation to feel sluggish and unresponsive.

**Advanced NVDA Remote** solves this by establishing direct **WebRTC Peer-to-Peer Data Channels** between the two instances. It enables:
*   **Near-Zero Latency**: Direct network speeds (<10ms on WAN, <1ms on LAN).
*   **Bypassing Relay Bottlenecks**: No central server bottlenecks or downtime affecting your active connection.
*   **Try peer-to-peer communication much faster than normal NVDA remote** for an instantly noticeable boost in responsiveness.

---

## Features

*   **Lightning-Fast WebRTC P2P Data Channels**: Direct socket connection for immediate keypress and speech response.
*   **Automatic Relay Fallback**: If a direct connection cannot be negotiated (due to strict symmetric NATs or corporate firewalls), the add-on silently falls back to standard relay routing. You get the speed of P2P whenever possible and the connection reliability of relay servers.
*   **Zero UI/UX Changes**: Integrates seamlessly with your existing NVDA. Connect exactly the same way using the standard connection dialogs (**NVDA+N -> Tools -> Remote -> Connect**).
*   **Statically Compiled Rust Core**: The underlying WebRTC connection engine is written in Rust, compiled into a single high-performance native binary (`p2p_webrtc.pyd`) for maximum efficiency.
*   **Built-in Encryption**: Leverages WebRTC's secure protocol design for encrypted peer-to-peer communication.

---

## Installation

1.  Download the latest packaged add-on: `advanced-nvda-remote.nvda-addon`.
2.  Open the file to install the add-on within NVDA.
3.  Restart NVDA when prompted.
4.  Connect normally using standard NVDA Remote menus. The add-on will automatically handle signaling and transition the session to a direct P2P link.

---

## Development & Building

If you wish to build the extension from source or contribute to its development, follow the steps below.

### Prerequisites
*   [Rust Toolchain](https://rustup.rs/) (Cargo)
*   Python 3.10+ (installed on Windows)

### Build and Package the Add-on
You can use the unified build automation script `build.py` at the root directory to build the WebRTC Rust extension, organize the binaries, and package the add-on:

1. **Build for Host Architecture only** (fast, recommended for local testing):
   ```powershell
   python build.py
   ```

2. **Build for all supported architectures** (x64, x86, and ARM64):
   ```powershell
   python build.py --all
   ```

3. **Build for a specific target**:
   ```powershell
   python build.py --target i686-pc-windows-msvc
   ```

---

## License

This project is licensed under the GNU General Public License (GPL) version 2, matching the license of NVDA. See the [LICENSE](LICENSE) file for details.
