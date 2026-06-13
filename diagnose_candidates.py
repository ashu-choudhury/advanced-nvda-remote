import sys
import os
import time

def main():
    arch = "x64" if sys.maxsize > 2**32 else "x86"
    lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addon", "lib", arch)
    sys.path.insert(0, lib_path)
    
    try:
        import p2p_webrtc
        print("[Diagnostic] Native WebRTC module loaded successfully.")
    except ImportError as e:
        print(f"[Diagnostic] Failed to load native WebRTC module: {e}")
        return

    print("[Diagnostic] Initializing WebRTC with Google STUN...")
    p2p_webrtc.init_leader(["stun:stun.l.google.com:19302"])
    
    print("[Diagnostic] Creating SDP Offer to trigger candidate gathering...")
    try:
        offer = p2p_webrtc.create_offer()
        print("[Diagnostic] Offer generated successfully.")
    except Exception as e:
        print(f"[Diagnostic] Failed to create offer: {e}")
        p2p_webrtc.close()
        return
    
    print("[Diagnostic] Waiting 6 seconds for candidate gathering & printing logs...")
    for _ in range(60):
        time.sleep(0.1)
        for log in p2p_webrtc.get_rust_logs():
            print(f"[Rust Log] {log}")
            
    candidates = p2p_webrtc.get_local_candidates()
    print(f"\n[Diagnostic] Gathered {len(candidates)} candidates:")
    for c in candidates:
        print(f"  {c}")
        
    p2p_webrtc.close()
    
    for log in p2p_webrtc.get_rust_logs():
        print(f"[Rust Log Post-Close] {log}")
        
    print("\n[Diagnostic] Done.")

if __name__ == "__main__":
    main()
