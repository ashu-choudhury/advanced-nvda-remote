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
*   Python 3.13 (installed on Windows)

### 1. Compile the WebRTC Rust Extension
Navigate to the `rust_src` folder and build the release target:
```powershell
cargo build --manifest-path rust_src/Cargo.toml --release
```

### 2. Copy the Native Binary
Rename and copy the compiled library into the add-on's library directory:
```powershell
Copy-Item -Path rust_src/target/release/p2p_webrtc.dll -Destination addon/lib/p2p_webrtc.pyd -Force
```

### 3. Package the Add-on
Run the packaging script at the root directory to generate the `.nvda-addon` file:
```powershell
python package_addon.py
```

---

## License

This project is licensed under the GNU General Public License (GPL) version 2, matching the license of NVDA. See the [LICENSE](LICENSE) file for details.
