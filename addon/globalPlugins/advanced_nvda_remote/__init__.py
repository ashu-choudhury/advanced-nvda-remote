import sys
import os
import platform
import struct
import threading
import time
import socket
import ssl
import select
import globalPluginHandler
import json
from . import p2p_file_transfer
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
addon_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
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

import ui
import _remoteClient
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
        self.file_receiver = None
        self.file_sender = None

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
        self.webrtc_started = False
        self.sig_candidates_sent.clear()
        last_audio_check = 0.0

        try:
            p2p_webrtc.close()
            log.info(f"P2P Override: Connecting to relay server {self.address} for signaling...")
            try:
                self.serverSock = self.createOutboundSocket(*self.address, insecure=self.insecure)
                self.serverSock.connect(self.address)
                self.relay_sock = self.serverSock
            except ssl.SSLCertVerificationError:
                fingerprint = None
                try:
                    fingerprint = self.getHostFingerprint()
                except Exception:
                    pass
                if self.isFingerprintTrusted(fingerprint):
                    self._trustedFingerprint = fingerprint
                    self.insecure = True
                    return self.run()
                self.lastFailFingerprint = fingerprint
                self.transportCertificateAuthenticationFailed.notify()
                raise
            except Exception as e:
                log.error(f"P2P Override: Failed to connect to relay server: {e}")
                self.transportConnectionFailed.notify()
                raise

            # If connecting without certificate verification and we were given a fingerprint to trust,
            # check that the server's certificate matches it.
            if (
                self.insecure
                and self._trustedFingerprint is not None
                and (fingerprint := self._derCert2fingerprint(self.serverSock.getpeercert(True)))
                != self._trustedFingerprint
            ):
                self._disconnect()
                self.lastFailFingerprint = fingerprint
                self.transportCertificateAuthenticationFailed.notify()
                self.transportConnectionFailed.notify()
                return

            self.onTransportConnected()
            self.startQueueThread()

            # Main read loop: select on relay socket first, then poll WebRTC once connected
            while not self.closed:
                # Poll for any Rust logs
                try:
                    for r_log in p2p_webrtc.get_rust_logs():
                        log.info(f"P2P Override [Rust]: {r_log}")
                except Exception as e:
                    log.error(f"P2P Override: Error getting Rust logs: {e}")

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
                    # Read from direct WebRTC Data Channel (combined control & file events)
                    try:
                        event = p2p_webrtc.recv_message()
                        if event:
                            channel, msg = event
                            if channel == 0:
                                # Append a newline because NVDA's deserializer expects lines
                                line = (msg + "\n").encode("utf-8")
                                self.parse(line)
                            elif channel == 1:
                                self.parse_file_message(msg)
                            elif channel == 2:
                                self.handle_file_chunk(msg)
                    except Exception as e:
                        log.error(f"P2P Override: Error in WebRTC message polling: {e}")
                        break
                    
                    # Check for disconnect
                    if not p2p_webrtc.is_connected():
                        log.warn("P2P Override: WebRTC Data Channel disconnected.")
                        break

                    # Periodically verify audio pipeline health (every 2 seconds)
                    now = time.time()
                    if now - last_audio_check > 2.0:
                        last_audio_check = now
                        try:
                            if not p2p_webrtc.is_audio_active() or p2p_webrtc.check_default_devices_changed():
                                log.warn("P2P Override: Audio pipeline inactive or default audio devices changed. Restarting...")
                                was_muted = p2p_webrtc.is_mic_muted()
                                p2p_webrtc.stop_audio()
                                p2p_webrtc.start_audio()
                                p2p_webrtc.set_mic_muted(was_muted)
                                log.info("P2P Override: Audio pipeline recovered successfully.")
                        except Exception as e:
                            log.error(f"P2P Override: Failed to recover audio pipeline: {e}")
        finally:
            log.info("P2P Override: Exited transport read loop. Cleaning up...")
            self.connected = False
            self.connectedEvent.clear()
            self.transportDisconnected.notify()
            self._disconnect()
            try:
                p2p_webrtc.stop_audio()
            except Exception:
                pass
            if hasattr(self, "file_receiver") and self.file_receiver:
                self.file_receiver.cleanup()
                self.file_receiver = None
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
                    if self.webrtc_started:
                        log.info("P2P Override: Peer present in channel but WebRTC is already started. Cleaning up stale session first...")
                        self.use_webrtc = False
                        self.webrtc_started = False
                        self.sig_candidates_sent.clear()
                        try:
                            p2p_webrtc.close()
                        except Exception as e:
                            log.error(f"P2P Override: Error closing stale WebRTC: {e}")
                    self.initiate_webrtc()
            elif msg_type == "client_joined":
                client = obj.get("client", {})
                peer_role = "slave" if self.connectionType in ("leader", "master") else "master"
                if client.get("connection_type") == peer_role:
                    if self.webrtc_started:
                        log.info("P2P Override: New peer joined but WebRTC is already started. Cleaning up stale session first...")
                        self.use_webrtc = False
                        self.webrtc_started = False
                        self.sig_candidates_sent.clear()
                        try:
                            p2p_webrtc.close()
                        except Exception as e:
                            log.error(f"P2P Override: Error closing stale WebRTC: {e}")
                    self.initiate_webrtc()
            elif msg_type == "client_left":
                client = obj.get("client", {})
                peer_role = "slave" if self.connectionType in ("leader", "master") else "master"
                if client.get("connection_type") == peer_role:
                    log.info("P2P Override: Peer disconnected. Resetting WebRTC status.")
                    self.use_webrtc = False
                    self.webrtc_started = False
                    self.sig_candidates_sent.clear()
                    try:
                        p2p_webrtc.close()
                    except Exception as e:
                        log.error(f"P2P Override: Error closing WebRTC: {e}")
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
                log.info(f"P2P Override: Received remote ICE candidate: {cand}")
                try:
                    p2p_webrtc.add_ice_candidate(cand)
                    log.info("P2P Override: Successfully added remote ICE candidate.")
                except Exception as e:
                    log.error(f"P2P Override: Failed to add remote ICE candidate: {e}")
                return
        except Exception as e:
            log.error(f"P2P Override: Error handling signaling packet: {e}")
        super().parse(line)

    def parse_file_message(self, msg_str):
        try:
            obj = json.loads(msg_str)
            msg_type = obj.get("type")
            if msg_type not in ("p2p_file_chunk", "p2p_file_ack"):
                log.info(f"P2P Override: parse_file_message msg_type: {msg_type}")
            if msg_type == "p2p_file_start":
                filename = obj.get("filename")
                size = obj.get("size")
                is_zip = obj.get("is_zip", False)
                log.info(f"P2P Override: Starting file receive. filename: {filename}, size: {size}, is_zip: {is_zip}")
                self.file_receiver = p2p_file_transfer.FileReceiver()
                self.file_receiver.start(filename, size, is_zip)
            elif msg_type == "p2p_file_chunk":
                data = obj.get("data")
                if hasattr(self, "file_receiver") and self.file_receiver:
                    chunk_len = self.file_receiver.write_chunk(data)
                    # Send ACK back to the sender
                    ack_payload = {
                        "type": "p2p_file_ack",
                        "bytes": chunk_len
                    }
                    success = p2p_webrtc.send_file_message(json.dumps(ack_payload))
            elif msg_type == "p2p_file_ack":
                ack_bytes = obj.get("bytes", 0)
                if hasattr(self, "file_sender") and self.file_sender:
                    self.file_sender.handle_ack(ack_bytes)
            elif msg_type == "p2p_file_end":
                log.info("P2P Override: Received file end message")
                if hasattr(self, "file_receiver") and self.file_receiver:
                    self.file_receiver.finalize()
                    self.file_receiver = None
            elif msg_type == "p2p_file_abort":
                log.info("P2P Override: Received file abort message")
                if hasattr(self, "file_receiver") and self.file_receiver:
                    self.file_receiver.abort()
                    self.file_receiver = None
                if hasattr(self, "file_sender") and self.file_sender:
                    self.file_sender.cancelled = True
            elif msg_type == "p2p_ping":
                timestamp = obj.get("timestamp")
                pong_payload = {
                    "type": "p2p_pong",
                    "timestamp": timestamp
                }
                p2p_webrtc.send_file_message(json.dumps(pong_payload))
            elif msg_type == "p2p_pong":
                timestamp = obj.get("timestamp", 0)
                if timestamp > 0:
                    rtt = (time.time() - timestamp) * 1000
                    ui.message(f"Ping: {rtt:.1f} ms")
        except Exception as e:
            log.error(f"P2P Override: Error parsing file message: {e}")

    def handle_file_chunk(self, data):
        try:
            if hasattr(self, "file_receiver") and self.file_receiver:
                chunk_len = self.file_receiver.write_chunk(data)
                # Send ACK back to the sender
                ack_payload = {
                    "type": "p2p_file_ack",
                    "bytes": chunk_len
                }
                success = p2p_webrtc.send_file_message(json.dumps(ack_payload))
        except Exception as e:
            log.error(f"P2P Override: Error handling file chunk: {e}")


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super().__init__()
        # Overwrite the built-in transport with our P2P WebRTC transport subclass!
        log.info("P2P Override: Installing monkey-patch for _remoteClient.client.RelayTransport...")
        _remoteClient.client.RelayTransport = P2PRelayTransport

        # Register local scripts
        try:
            if _remoteClient._remoteClient is not None:
                _remoteClient._remoteClient.registerLocalScript(self.script_toggleMicrophone)
                _remoteClient._remoteClient.registerLocalScript(self.script_cancelFileTransfer)
                _remoteClient._remoteClient.registerLocalScript(self.script_pingPeer)
                log.info("P2P Override: Registered local scripts.")
        except Exception as e:
            log.error(f"P2P Override: Failed to register local scripts: {e}")

        # Monkey-patch pushClipboard to support file transfer
        try:
            self.original_pushClipboard = _remoteClient.client.RemoteClient.pushClipboard
            
            def patched_pushClipboard(client_inst):
                files = p2p_file_transfer.get_files_from_clipboard()
                log.info(f"P2P Override: patched_pushClipboard called. files: {files}")
                if files:
                    transport = client_inst.followerTransport or client_inst.leaderTransport
                    use_webrtc = getattr(transport, "use_webrtc", False) if transport else False
                    has_file_channel = p2p_webrtc.has_file_channel() if webrtc_available else False
                    log.info(f"P2P Override: transport: {transport}, use_webrtc: {use_webrtc}, has_file_channel: {has_file_channel}")
                    if transport and use_webrtc and has_file_channel:
                        sender = p2p_file_transfer.FileSenderThread(
                            files,
                            p2p_webrtc.send_file_message,
                            p2p_webrtc.send_file_chunk,
                            p2p_webrtc.get_file_buffered_amount
                        )
                        transport.file_sender = sender
                        sender.start()
                    else:
                        log.info("P2P Override: Condition not met. Falling back to original pushClipboard.")
                        self.original_pushClipboard(client_inst)
                else:
                    self.original_pushClipboard(client_inst)
                    
            _remoteClient.client.RemoteClient.pushClipboard = patched_pushClipboard
            log.info("P2P Override: Monkey-patched RemoteClient.pushClipboard for file transfers.")
        except Exception as e:
            log.error(f"P2P Override: Failed to patch pushClipboard: {e}")

    def terminate(self):
        # Unregister local scripts
        try:
            if _remoteClient._remoteClient is not None:
                _remoteClient._remoteClient.unregisterLocalScript(self.script_toggleMicrophone)
                _remoteClient._remoteClient.unregisterLocalScript(self.script_cancelFileTransfer)
                _remoteClient._remoteClient.unregisterLocalScript(self.script_pingPeer)
                log.info("P2P Override: Unregistered local scripts.")
        except Exception as e:
            log.error(f"P2P Override: Failed to unregister local scripts: {e}")

        # Restore original pushClipboard
        try:
            if hasattr(self, "original_pushClipboard"):
                _remoteClient.client.RemoteClient.pushClipboard = self.original_pushClipboard
                log.info("P2P Override: Restored original RemoteClient.pushClipboard.")
        except Exception as e:
            log.error(f"P2P Override: Failed to restore pushClipboard: {e}")

        super().terminate()

    def script_toggleMicrophone(self, gesture):
        if not webrtc_available:
            return
        
        try:
            if not p2p_webrtc.is_connected():
                ui.message("Not connected to a P2P remote session")
                return
            
            current_mute = p2p_webrtc.is_mic_muted()
            new_mute = not current_mute
            p2p_webrtc.set_mic_muted(new_mute)
            
            if new_mute:
                ui.message("Microphone muted")
            else:
                ui.message("Microphone unmuted")
        except Exception as e:
            log.error(f"P2P Override: Error toggling microphone: {e}")

    # Set script category and gesture bindings
    script_toggleMicrophone.category = "P2P NVDA Remote"
    script_toggleMicrophone.__doc__ = "Toggles the microphone state for voice communication during a P2P session."
    
    def script_cancelFileTransfer(self, gesture):
        client_inst = _remoteClient._remoteClient
        if not client_inst:
            ui.message("Not connected")
            return
        transport = client_inst.followerTransport or client_inst.leaderTransport
        if not transport or not getattr(transport, "use_webrtc", False):
            ui.message("Not connected to a P2P remote session")
            return
            
        cancelled_any = False
        if hasattr(transport, "file_sender") and transport.file_sender and transport.file_sender.is_alive():
            transport.file_sender.cancelled = True
            cancelled_any = True
            
        if hasattr(transport, "file_receiver") and transport.file_receiver:
            transport.file_receiver.abort()
            transport.file_receiver = None
            p2p_webrtc.send_file_message(json.dumps({"type": "p2p_file_abort"}))
            cancelled_any = True
            
        if cancelled_any:
            ui.message("File transfer cancelled")
        else:
            ui.message("No active file transfer")

    script_cancelFileTransfer.category = "P2P NVDA Remote"
    script_cancelFileTransfer.__doc__ = "Cancels the active P2P file transfer."

    def script_pingPeer(self, gesture):
        client_inst = _remoteClient._remoteClient
        if not client_inst:
            ui.message("Not connected")
            return
        transport = client_inst.followerTransport or client_inst.leaderTransport
        if not transport or not getattr(transport, "use_webrtc", False):
            ui.message("Not connected to a P2P remote session")
            return
        
        # Send ping request
        timestamp = time.time()
        payload = {
            "type": "p2p_ping",
            "timestamp": timestamp
        }
        if p2p_webrtc.send_file_message(json.dumps(payload)):
            pass
        else:
            ui.message("Failed to send ping")

    script_pingPeer.category = "P2P NVDA Remote"
    script_pingPeer.__doc__ = "Pings the peer device to measure latency."
    
    __gestures = {
        "kb:control+shift+m": "toggleMicrophone",
        "kb:control+shift+c": "cancelFileTransfer",
        "kb:nvda+alt+p": "pingPeer"
    }
