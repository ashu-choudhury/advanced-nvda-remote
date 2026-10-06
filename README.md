# Advanced NVDA Remote

**Advanced NVDA Remote** is a fast, feature-rich upgrade for the standard NVDA Remote access tool. By establishing direct peer-to-peer (P2P) connections via WebRTC, it provides a significantly more responsive remote control experience, crystal-clear voice communication with acoustic echo cancellation, and direct clipboard file transfer.

---

## Why Use Advanced NVDA Remote?

* **Eliminate Lag:** Standard NVDA Remote routes actions through a central relay server far from users, which introduces notable latency (typically 200–500ms). This add-on establishes a direct P2P connection, bringing lag down to 50 milliseconds or less.
* **Direct Connections:** Remote control inputs, speech, and braille are sent directly between devices, making remote screen reader navigation feel near-instant.
* **Automatic Fallback:** If a direct P2P connection cannot be established due to symmetric NATs or firewall restrictions, the add-on seamlessly continues using the standard relay server so you never lose connection.

---

## Key Features

* **Direct Peer-to-Peer Remote Control:** Offers near-instant keypress responses and smooth screen reader navigation.
* **Built-in Voice Chat:** Speak with the remote user directly within the session with hardware-accelerated Opus encoding and Sonora echo cancellation / noise suppression.
* **Fast File Transfer:** Easily copy and paste files of any size directly through the clipboard with stream compression and flow control.
* **Seamless Self-Updates:** Automatically checks GitHub releases for updates without conflicting with NVDA's native Add-on Store or Add-on Updater. You can also manually check anytime via **NVDA Menu (NVDA + N) -> Tools -> Check for Advanced NVDA Remote update...**.

---

## Keyboard Shortcuts

These shortcuts can be used during an active P2P remote session (customizable in NVDA's **Input Gestures** dialog under **Advanced NVDA Remote**):

* **Toggle Microphone (`NVDA + Alt + M`):** Mutes or unmutes your microphone.
* **Cancel File Transfer (`NVDA + Alt + C`):** Cancels the active file transfer.
* **Ping Peer (`NVDA + Alt + P`):** Measures the round-trip network latency (ping) directly between devices and announces the result.

---

## Installation & Usage

### Installation
1. Ensure the base **NVDA Remote** add-on is installed.
2. Download `advanced-nvda-remote.nvda-addon`.
3. Open the file to install it in NVDA.
4. Restart NVDA.

### How to Use
1. Open the NVDA menu (**NVDA + N**), go to **Tools -> Remote -> Connect**.
2. Enter your connection details as usual and connect.
3. The add-on will automatically establish the direct P2P link and activate the voice and file transfer features.

---

## Development

If you want to build the add-on from source, you need the Rust toolchain and Python 3.10+ installed on Windows.

Run the build script from the root directory:
* **Build for your PC:** `python build.py`
* **Build for all PCs (x64, x86, ARM64):** `python build.py --all`
* **Run test suite:** `python build.py --test`

---

## License

This project is licensed under the GNU General Public License (GPL) version 2. See the [LICENSE](LICENSE) file for details.
