import sys
import os
import subprocess
import shutil
import platform
import struct

# Force the Rust cmake crate to use our wrapper script
os.environ["CMAKE"] = os.path.abspath("cmake_wrapper.bat")

# Set the native CMake environment overrides to bypass deprecation policies
os.environ["CMAKE_POLICY_VERSION_MINIMUM"] = "3.5"
os.environ["CMAKE_VAR_CMAKE_POLICY_VERSION_MINIMUM"] = "3.5"

def get_host_arch():
    # Detect host processor architecture and bitness
    machine = platform.machine().lower()
    is_64bit = struct.calcsize("P") == 8
    
    if not is_64bit:
        return "x86", "i686-pc-windows-msvc"
    
    if "arm" in machine or "aarch64" in machine:
        return "arm64", "aarch64-pc-windows-msvc"
    else:
        return "x64", "x86_64-pc-windows-msvc"

def compile_and_copy():
    arch_folder, target_name = get_host_arch()
    print(f"Building for host target {target_name} ({arch_folder})...")
    
    # Ensure rustup target is installed
    try:
        subprocess.run(["rustup", "target", "add", target_name], check=True)
    except Exception as e:
        print(f"Warning: Failed to run rustup target add {target_name}: {e}")
        
    cmd = [
        "cargo", "build",
        "--manifest-path", "rust_src/Cargo.toml",
        "--target", target_name,
        "--release"
    ]
    subprocess.run(cmd, check=True)
    
    # Path to release DLL
    dll_path = os.path.join("rust_src", "target", target_name, "release", "p2p_webrtc.dll")
    if not os.path.exists(dll_path):
        raise FileNotFoundError(f"Could not find compiled binary at {dll_path}")
        
    dest_dir = os.path.join("addon", "lib", arch_folder)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, "p2p_webrtc.pyd")
    
    print(f"Copying {dll_path} to {dest_path}")
    shutil.copy2(dll_path, dest_path)

def package():
    # Import and run package function from package_addon.py
    sys.path.append(os.path.dirname(__file__))
    import package_addon
    package_addon.package()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build and package advanced-nvda-remote add-on")
    parser.add_argument("--test", action="store_true", help="Run the Rust test suite")
    args = parser.parse_args()
    
    if args.test:
        arch_folder, target_name = get_host_arch()
        print(f"Running cargo test for target {target_name}...")
        cmd = [
            "cargo", "test",
            "--manifest-path", "rust_src/Cargo.toml",
            "--target", target_name,
            "--", "--nocapture"
        ]
        try:
            subprocess.run(cmd, check=True)
            print("Tests completed successfully!")
            sys.exit(0)
        except Exception as e:
            print(f"Error running tests: {e}")
            sys.exit(1)

    try:
        compile_and_copy()
    except Exception as e:
        print(f"Error building for host target: {e}")
        sys.exit(1)
                
    # Run packaging
    print("Packaging add-on...")
    package()

if __name__ == "__main__":
    main()
