# Advanced NVDA Remote

**Advanced NVDA Remote** is a fast, feature-rich upgrade for the standard NVDA Remote access tool. By establishing direct peer-to-peer (P2P) connections, it provides a much more responsive remote control experience and adds built-in voice chat and file transfer.

---

## Why Use Advanced NVDA Remote?

* **Eliminate Lag:** Standard NVDA Remote routes actions through a central server far from users, which can introduce a lag of around 400 milliseconds. This add-on establishes a direct connection, bringing lag down to 50 milliseconds or less.
* **Direct Connections:** Remote control inputs, speech, and braille are sent directly between devices, making remote navigation feel fast and smooth.
* **Automatic Fallback:** If a direct connection cannot be made due to firewall restrictions, the add-on automatically uses the standard relay server so you can always connect.

---

## Key Features

* **Direct Peer-to-Peer Remote Control:** Offers near-instant keypress responses and smooth screen reader navigation.
* **Built-in Voice Chat:** Speak with the remote user directly within the session.
* **Fast File Transfer:** Easily copy and paste files of any size directly through the clipboard.
---

## Keyboard Shortcuts

These shortcuts can be used during a remote session:

* **Toggle Microphone (`Control + Shift + M`):** Mutes or unmutes your microphone.
* **Cancel File Transfer (`Control + Shift + C`):** Cancels the active file transfer.
* **Ping Peer (`Alt + NVDA + P`):** Measures the connection speed (ping) between devices and speaks/displays the result.

---

## Installation & Usage

### Installation
1. Download `advanced-nvda-remote.nvda-addon`.
2. Open the file to install it in NVDA.
3. Restart NVDA.

### How to Use
1. Open the NVDA menu (**NVDA+N**), go to **Tools -> Remote -> Connect**.
2. Enter your connection details as usual and connect.
3. The add-on will automatically establish the direct P2P link and activate the voice and file transfer features.

---

## Development

If you want to build the add-on from source, you need the Rust toolchain and Python 3.10+ installed on Windows.

Run the build script from the root directory:
* **Build for your PC:** `python build.py`
* **Build for all PCs (x64, x86, ARM64):** `python build.py --all`

---

## License

This project is licensed under the GNU General Public License (GPL) version 2, See the [LICENSE](LICENSE) file for details.


