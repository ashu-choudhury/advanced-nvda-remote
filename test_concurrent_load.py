import sys
import os
import time
import json
import subprocess
import threading
import zlib
import base64

def add_lib_path():
    arch = "x64" if sys.maxsize > 2**32 else "x86"
    # Add project root and addon lib to path
    project_dir = os.path.dirname(os.path.abspath(__file__))
    lib_path = os.path.join(project_dir, "addon", "lib", arch)
    sys.path.insert(0, lib_path)
    sys.path.insert(0, os.path.join(project_dir, "addon", "globalPlugins"))
    print(f"[Python] Added library paths: {lib_path}", flush=True)

def run_leader():
    add_lib_path()
    import p2p_webrtc
    
    # Mock clipboard write in p2p_file_transfer if it gets imported
    try:
        from advanced_nvda_remote import p2p_file_transfer
        p2p_file_transfer.set_clipboard_hdrop_and_move = lambda paths: True
    except Exception as e:
        print(f"[Leader] Warning overriding clipboard: {e}", flush=True)

    print("[Leader] Initializing leader...", flush=True)
    p2p_webrtc.init_leader(["stun:stun.l.google.com:19302"])

    print("[Leader] Creating offer...", flush=True)
    offer = p2p_webrtc.create_offer()
    print(f"OFFER:{offer}", flush=True)

    # Read answer
    answer_line = sys.stdin.readline().strip()
    if answer_line.startswith("ANSWER:"):
        answer = answer_line[len("ANSWER:"):]
        p2p_webrtc.set_answer(answer)

    # Thread to handle incoming candidates from coordinator
    def candidate_reader():
        for line in sys.stdin:
            line = line.strip()
            if line.startswith("CANDIDATE:"):
                cand = line[len("CANDIDATE:"):]
                try:
                    p2p_webrtc.add_ice_candidate(cand)
                except Exception as e:
                    pass

    t = threading.Thread(target=candidate_reader, daemon=True)
    t.start()

    # Wait for connection
    connected = False
    for _ in range(250):
        for cand in p2p_webrtc.get_local_candidates():
            print(f"CANDIDATE:{cand}", flush=True)
        if p2p_webrtc.is_connected():
            connected = True
            print("[Leader] Connected!", flush=True)
            break
        time.sleep(0.1)

    if not connected:
        print("[Leader] Failed to connect", flush=True)
        return

    # Start audio pipeline on leader
    print("[Leader] Starting audio stream...", flush=True)
    try:
        p2p_webrtc.start_audio()
        p2p_webrtc.set_mic_muted(false_value := False)
    except Exception as e:
        print(f"[Leader] Audio stream init error (headless host?): {e}", flush=True)

    # File sending simulation: 100 MB
    total_size = 100 * 1024 * 1024 # 100MB
    chunk_size = 32768 # 32KB (safe limit for WebRTC data channels to account for zlib overhead)
    bytes_sent = 0
    bytes_acked = 0
    ack_lock = threading.Lock()

    def handle_leader_acks():
        nonlocal bytes_acked
        while p2p_webrtc.is_connected():
            try:
                # We also need to poll recv_message on the leader to receive ACKs from the follower!
                event = p2p_webrtc.recv_message()
                if event:
                    channel, msg_str = event
                    if channel == 1:
                        obj = json.loads(msg_str)
                        if obj.get("type") == "p2p_file_ack":
                            with ack_lock:
                                bytes_acked += obj.get("bytes", 0)
            except Exception as e:
                break

    ack_thread = threading.Thread(target=handle_leader_acks, daemon=True)
    ack_thread.start()

    # Send start message
    start_payload = {
        "type": "p2p_file_start",
        "filename": "load_test.bin",
        "size": total_size,
        "is_zip": False
    }
    p2p_webrtc.send_file_message(json.dumps(start_payload))

    # Control messages sender thread
    def control_sender():
        for i in range(50):
            if not p2p_webrtc.is_connected():
                break
            p2p_webrtc.send_message(f"CTRL_MSG_{i}")
            time.sleep(0.05) # 50ms interval

    ctrl_thread = threading.Thread(target=control_sender, daemon=True)
    ctrl_thread.start()

    # Send file chunks
    print("[Leader] Transferring 100MB file...", flush=True)
    dummy_data = b"\x00" * chunk_size
    compressed_chunk = zlib.compress(dummy_data, 1)
    
    while bytes_sent < total_size and p2p_webrtc.is_connected():
        # Flow control
        with ack_lock:
            diff = bytes_sent - bytes_acked
        while (diff > 1024 * 1024 or p2p_webrtc.get_file_buffered_amount() > 524288) and p2p_webrtc.is_connected():
            time.sleep(0.01)
            with ack_lock:
                diff = bytes_sent - bytes_acked

        p2p_webrtc.send_file_chunk(compressed_chunk)
        bytes_sent += chunk_size

    # Send end message
    p2p_webrtc.send_file_message(json.dumps({"type": "p2p_file_end"}))
    print("[Leader] File transfer completed.", flush=True)

    # Let the control thread finish
    ctrl_thread.join()
    time.sleep(2)
    p2p_webrtc.close()

def run_follower():
    add_lib_path()
    import p2p_webrtc
    
    # Mock clipboard write in p2p_file_transfer if it gets imported
    try:
        from advanced_nvda_remote import p2p_file_transfer
        p2p_file_transfer.set_clipboard_hdrop_and_move = lambda paths: True
    except Exception as e:
        print(f"[Follower] Warning overriding clipboard: {e}", flush=True)

    print("[Follower] Initializing follower...", flush=True)
    p2p_webrtc.init_follower(["stun:stun.l.google.com:19302"])

    offer_line = sys.stdin.readline().strip()
    if offer_line.startswith("OFFER:"):
        offer = offer_line[len("OFFER:"):]
        p2p_webrtc.set_offer(offer)
        answer = p2p_webrtc.create_answer()
        print(f"ANSWER:{answer}", flush=True)

    # Thread to handle incoming candidates from coordinator
    def candidate_reader():
        for line in sys.stdin:
            line = line.strip()
            if line.startswith("CANDIDATE:"):
                cand = line[len("CANDIDATE:"):]
                try:
                    p2p_webrtc.add_ice_candidate(cand)
                except Exception as e:
                    pass

    t = threading.Thread(target=candidate_reader, daemon=True)
    t.start()

    # Wait for connection
    connected = False
    for _ in range(250):
        for cand in p2p_webrtc.get_local_candidates():
            print(f"CANDIDATE:{cand}", flush=True)
        if p2p_webrtc.is_connected():
            connected = True
            print("[Follower] Connected!", flush=True)
            break
        time.sleep(0.1)

    if not connected:
        print("[Follower] Failed to connect", flush=True)
        return

    # Start audio pipeline on follower
    print("[Follower] Starting audio stream...", flush=True)
    try:
        p2p_webrtc.start_audio()
        p2p_webrtc.set_mic_muted(false_value := False)
    except Exception as e:
        print(f"[Follower] Audio stream init error (headless host?): {e}", flush=True)

    # Receiver state variables
    control_messages_received = []
    file_bytes_written = 0
    file_completed = False

    # Simulate read loop
    print("[Follower] Starting concurrent read loop...", flush=True)
    while p2p_webrtc.is_connected():
        try:
            event = p2p_webrtc.recv_message()
            if event:
                channel, msg = event
                if channel == 0:
                    control_messages_received.append(msg)
                elif channel == 1:
                    obj = json.loads(msg)
                    msg_type = obj.get("type")
                    if msg_type == "p2p_file_start":
                        pass
                    elif msg_type == "p2p_file_chunk":
                        # Support older base64 text chunks
                        data = obj.get("data")
                        compressed = base64.b64decode(data)
                        chunk = zlib.decompress(compressed)
                        chunk_len = len(chunk)
                        file_bytes_written += chunk_len
                        
                        # Send ACK back
                        ack_payload = {
                            "type": "p2p_file_ack",
                            "bytes": chunk_len
                        }
                        p2p_webrtc.send_file_message(json.dumps(ack_payload))
                    elif msg_type == "p2p_file_end":
                        file_completed = True
                elif channel == 2:
                    # Raw binary chunk
                    chunk = zlib.decompress(msg)
                    chunk_len = len(chunk)
                    file_bytes_written += chunk_len
                    
                    # Send ACK back
                    ack_payload = {
                        "type": "p2p_file_ack",
                        "bytes": chunk_len
                    }
                    p2p_webrtc.send_file_message(json.dumps(ack_payload))
        except Exception as e:
            print(f"[Follower] Error in read loop: {e}", flush=True)
            break

    audio_active = False
    try:
        audio_active = p2p_webrtc.is_audio_active()
        print(f"[Follower] Audio pipeline active: {audio_active}", flush=True)
    except Exception as e:
        print(f"[Follower] Error checking audio active: {e}", flush=True)

    if file_completed and file_bytes_written == 100 * 1024 * 1024 and len(control_messages_received) == 50 and audio_active:
        print("[Follower] LOAD_TEST_PASS", flush=True)
    else:
        print(f"[Follower] LOAD_TEST_FAIL (file={file_completed}, size={file_bytes_written}, control={len(control_messages_received)}, audio={audio_active})", flush=True)

    p2p_webrtc.close()

def run_coordinator():
    print("[Coordinator] Starting 100MB concurrent load test...", flush=True)
    force_internet = "--force-internet" in sys.argv
    if force_internet:
        print("[Coordinator] Force Internet mode: ONLY exchanging STUN (srflx) and TURN (relay) candidates.", flush=True)
    leader_cmd = [sys.executable, __file__, "--leader"]
    follower_cmd = [sys.executable, __file__, "--follower"]

    leader = subprocess.Popen(leader_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    follower = subprocess.Popen(follower_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    offer = None
    answer = None

    def route_leader():
        nonlocal offer
        for line in leader.stdout:
            line = line.strip()
            print(f"[Leader] {line}", flush=True)
            if line.startswith("OFFER:"):
                offer = line
                follower.stdin.write(offer + "\n")
                follower.stdin.flush()
            elif line.startswith("CANDIDATE:"):
                if force_internet:
                    if "typ srflx" in line or "typ relay" in line:
                        follower.stdin.write(line + "\n")
                        follower.stdin.flush()
                else:
                    follower.stdin.write(line + "\n")
                    follower.stdin.flush()

    def route_follower():
        nonlocal answer
        for line in follower.stdout:
            line = line.strip()
            print(f"[Follower] {line}", flush=True)
            if line.startswith("ANSWER:"):
                answer = line
                leader.stdin.write(answer + "\n")
                leader.stdin.flush()
            elif line.startswith("CANDIDATE:"):
                if force_internet:
                    if "typ srflx" in line or "typ relay" in line:
                        leader.stdin.write(line + "\n")
                        leader.stdin.flush()
                else:
                    leader.stdin.write(line + "\n")
                    leader.stdin.flush()

    lt = threading.Thread(target=route_leader, daemon=True)
    ft = threading.Thread(target=route_follower, daemon=True)
    lt.start()
    ft.start()

    # Timeout of 45 seconds to compile and transfer 100MB over local RTC loopback
    timeout = 45
    start_time = time.time()
    test_passed = False

    while time.time() - start_time < timeout:
        if leader.poll() is not None and follower.poll() is not None:
            break
        # We can scan the follower's stdout for the pass keyword
        time.sleep(0.1)

    if leader.poll() is None:
        leader.terminate()
    if follower.poll() is None:
        follower.terminate()

    # Read remaining stderr to print debug logs if any process failed
    l_err = leader.stderr.read()
    f_err = follower.stderr.read()
    if l_err:
        print(f"[Leader Error]\n{l_err}", flush=True)
    if f_err:
        print(f"[Follower Error]\n{f_err}", flush=True)

    print("[Coordinator] Load test finished.", flush=True)

if __name__ == "__main__":
    if "--leader" in sys.argv:
        run_leader()
    elif "--follower" in sys.argv:
        run_follower()
    else:
        run_coordinator()
