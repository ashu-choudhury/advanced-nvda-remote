import sys
import os
import platform
import struct
import threading
import time
import socket
import select
import globalPluginHandler
from logHandler import log

def get_architecture_folder():
    machine = platform.machine().lower()
    is_64bit = struct.calcsize("P") == 8
    
    if not is_64bit:
        # 32-bit processes (like NVDA x86 under WoW64) can only load x86 native modules
        return "x86"
    
    # 64-bit processes: check for ARM64 vs standard AMD64/x86_64
    if "arm" in machine or "aarch64" in machine:
        return "arm64"
    else:
        return "x64"

# Inject the architecture-specific lib folder into sys.path
addon_root = os.path.dirname(os.path.dirname(__file__))
arch_folder = get_architecture_folder()
lib_dir = os.path.join(addon_root, "lib", arch_folder)
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

# Also fallback to root lib directory for backward compatibility
fallback_lib_dir = os.path.join(addon_root, "lib")
if fallback_lib_dir not in sys.path:
    sys.path.append(fallback_lib_dir)

try:
    import p2p_webrtc
    webrtc_available = True
    log.info("P2P Override: Successfully loaded native WebRTC module.")
except ImportError as e:
    webrtc_available = False
    log.error(f"P2P Override: Failed to load native WebRTC module: {e}")

import _remoteClient.client
from _remoteClient.transport import RelayTransport
from _remoteClient.protocol import RemoteMessageType

class P2PRelayTransport(RelayTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_webrtc = False
        self.relay_sock = None
        self.sig_candidates_sent = set()
        self.webrtc_started = False

    def initiate_webrtc(self):
        if self.webrtc_started:
            return
        self.webrtc_started = True
        
        stun_servers = ["stun:stun.l.google.com:19302"]
        if self.connectionType in ("leader", "master"):
            log.info("P2P Override: Initializing WebRTC Leader...")
            p2p_webrtc.init_leader(stun_servers)
            try:
                offer = p2p_webrtc.create_offer()
                log.info("P2P Override: Created SDP offer, sending via relay...")
                self.send_to_relay(type="p2p_sdp", sdp=offer)
            except Exception as e:
                log.error(f"P2P Override: Failed to create WebRTC offer: {e}")
        else:
            log.info("P2P Override: Initializing WebRTC Follower...")
            p2p_webrtc.init_follower(stun_servers)

    def run(self) -> None:
        if not webrtc_available:
            log.warn("P2P Override: WebRTC module not available. Falling back to default Relay mode.")
            super().run()
            return

        self.closed = False
        self.use_webrtc = False
        self.sig_candidates_sent.clear()
        p2p_webrtc.close()
        
        log.info(f"P2P Override: Connecting to relay server {self.address} for signaling...")
        try:
            self.serverSock = self.createOutboundSocket(*self.address, insecure=self.insecure)
            self.serverSock.connect(self.address)
            self.relay_sock = self.serverSock
        except Exception as e:
            log.error(f"P2P Override: Failed to connect to relay server: {e}")
            self.transportConnectionFailed.notify()
            raise

        self.onTransportConnected()
        self.startQueueThread()

        # Main read loop: select on relay socket first, then poll WebRTC once connected
        while not self.closed:
            if not self.use_webrtc:
                # Read from relay socket
                try:
                    readers, _, error = select.select([self.relay_sock], [], [self.relay_sock], 0.1)
                except (socket.error, ValueError):
                    break
                if self.relay_sock in error:
                    break
                if self.relay_sock in readers:
                    try:
                        self.processIncomingSocketData()
                    except socket.error:
                        break
                
                # Check for gathered local ICE candidates to transmit
                try:
                    candidates = p2p_webrtc.get_local_candidates()
                    for cand in candidates:
                        if cand not in self.sig_candidates_sent:
                            self.sig_candidates_sent.add(cand)
                            log.info(f"P2P Override: Sending gathered ICE candidate...")
                            self.send_to_relay(type="p2p_ice", candidate=cand)
                except Exception as e:
                    log.error(f"P2P Override: Error gathering ICE candidates: {e}")

                # Check if WebRTC P2P Data Channel has connected
                if p2p_webrtc.is_connected():
                    log.info("P2P Override: WebRTC Data Channel is connected! Swapping transport...")
                    self.use_webrtc = True
                    # Safely close the relay server socket
                    with self.serverSockLock:
                        if self.relay_sock:
                            self.relay_sock.close()
                            self.relay_sock = None
                            self.serverSock = None
                    log.info("P2P Override: Relay socket closed. Direct WebRTC P2P channel is active.")
                    try:
                        p2p_webrtc.start_audio()
                        log.info("P2P Override: Audio pipeline started successfully.")
                    except Exception as e:
                        log.error(f"P2P Override: Failed to start audio pipeline: {e}")
            else:
                # Read from direct WebRTC Data Channel
                try:
                    msg = p2p_webrtc.recv_message()
                    if msg:
                        # Append a newline because NVDA's deserializer expects lines
                        line = (msg + "\n").encode("utf-8")
                        self.parse(line)
                    else:
                        time.sleep(0.005) # Prevent CPU spinning
                except Exception as e:
                    log.error(f"P2P Override: Error in WebRTC message polling: {e}")
                    break
                
                # Check for disconnect
                if not p2p_webrtc.is_connected():
                    log.warn("P2P Override: WebRTC Data Channel disconnected.")
                    break

        log.info("P2P Override: Exited transport read loop. Cleaning up...")
        self.connected = False
        self.connectedEvent.clear()
        self.transportDisconnected.notify()
        self._disconnect()
        try:
            p2p_webrtc.stop_audio()
        except Exception:
            pass
        p2p_webrtc.close()

    def send_to_relay(self, type, **kwargs):
        """Helper to send packets strictly to the relay server during signaling phase."""
        obj = self.serializer.serialize(type=type, **kwargs)
        self.queue.put(obj)

    def send(self, type: RemoteMessageType, **kwargs):
        """Override standard send to route packets via WebRTC once swapped."""
        if self.use_webrtc:
            try:
                obj_bytes = self.serializer.serialize(type=type, **kwargs)
                # Remove trailing newline for cleaner WebRTC string frames
                msg_str = obj_bytes.decode("utf-8").rstrip("\n")
                p2p_webrtc.send_message(msg_str)
            except Exception as e:
                log.error(f"P2P Override: Failed to send WebRTC message: {e}")
        else:
            super().send(type, **kwargs)

    def parse(self, line: bytes) -> None:
        """Override parse to intercept custom signaling packets before the standard session sees them."""
        try:
            obj = self.serializer.deserialize(line)
            msg_type = obj.get("type")
            if msg_type == "channel_joined":
                clients = obj.get("clients", [])
                peer_role = "slave" if self.connectionType in ("leader", "master") else "master"
                has_peer = any(c.get("connection_type") == peer_role for c in clients)
                if has_peer:
                    self.initiate_webrtc()
            elif msg_type == "client_joined":
                client = obj.get("client", {})
                peer_role = "slave" if self.connectionType in ("leader", "master") else "master"
                if client.get("connection_type") == peer_role:
                    self.initiate_webrtc()
            elif msg_type == "p2p_sdp":
                sdp = obj.get("sdp")
                self.initiate_webrtc()
                if self.connectionType in ("follower", "slave"):
                    log.info("P2P Override: Received SDP offer. Setting remote description...")
                    p2p_webrtc.set_offer(sdp)
                    log.info("P2P Override: Creating SDP answer...")
                    answer = p2p_webrtc.create_answer()
                    self.send_to_relay(type="p2p_sdp", sdp=answer)
                else:
                    log.info("P2P Override: Received SDP answer. Setting remote description...")
                    p2p_webrtc.set_answer(sdp)
                return
            elif msg_type == "p2p_ice":
                cand = obj.get("candidate")
                p2p_webrtc.add_ice_candidate(cand)
                return
        except Exception as e:
            log.error(f"P2P Override: Error handling signaling packet: {e}")
        super().parse(line)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super().__init__()
        # Overwrite the built-in transport with our P2P WebRTC transport subclass!
        log.info("P2P Override: Installing monkey-patch for _remoteClient.client.RelayTransport...")
        _remoteClient.client.RelayTransport = P2PRelayTransport

    def script_toggleMicrophone(self, gesture):
        if not webrtc_available:
            return
        
        try:
            if not p2p_webrtc.is_connected():
                import ui
                ui.message("Not connected to a P2P remote session")
                return
            
            current_mute = p2p_webrtc.is_mic_muted()
            new_mute = not current_mute
            p2p_webrtc.set_mic_muted(new_mute)
            
            import winsound
            import ui
            if new_mute:
                winsound.Beep(800, 100)
                winsound.Beep(500, 150)
                ui.message("Microphone muted")
            else:
                winsound.Beep(500, 100)
                winsound.Beep(800, 150)
                ui.message("Microphone unmuted")
        except Exception as e:
            log.error(f"P2P Override: Error toggling microphone: {e}")

    # Set script category and gesture bindings
    script_toggleMicrophone.category = "P2P NVDA Remote"
    script_toggleMicrophone.__doc__ = "Toggles the microphone state for voice communication during a P2P session."
    
    __gestures = {
        "kb:control+shift+m": "toggleMicrophone"
    }
