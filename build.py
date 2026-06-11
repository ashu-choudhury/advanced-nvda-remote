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

# PYO3 environment variables are configured per-target in compile_and_copy

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

def compile_and_copy(target_name, arch_folder):
    print(f"Building for target {target_name}...")
    
    # Clean previous build artifacts to prevent cross-contamination of host/target libraries
    print("Cleaning previous build artifacts...")
    try:
        subprocess.run(["cargo", "clean", "--manifest-path", "rust_src/Cargo.toml"], check=True)
    except Exception as e:
        print(f"Warning: Failed to run cargo clean: {e}")
        
    # Ensure rustup target is installed
    try:
        subprocess.run(["rustup", "target", "add", target_name], check=True)
    except Exception as e:
        print(f"Warning: Failed to run rustup target add {target_name}: {e}")
        
    # Prepare environment and features dynamically
    env = os.environ.copy()
    host_arch, host_target = get_host_arch()
    is_cross = (target_name != host_target)
    
    cmd = [
        "cargo", "build",
        "--manifest-path", "rust_src/Cargo.toml",
        "--target", target_name,
        "--release"
    ]
    
    if is_cross:
        print(f"Cross-compilation detected for target {target_name}. Enabling PyO3 generate-import-lib feature.")
        env["PYO3_NO_PYTHON"] = "1"
        env["PYO3_BUILD_EXTENSION_MODULE"] = "1"
        cmd.extend(["--features", "generate-import-lib"])
    else:
        print(f"Host architecture compilation detected. Using local Python environment.")
        # Ensure we don't inherit cross-compilation overrides
        env.pop("PYO3_NO_PYTHON", None)
        env.pop("PYO3_BUILD_EXTENSION_MODULE", None)
        
    subprocess.run(cmd, env=env, check=True)
    
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
    parser.add_argument("--all", action="store_true", help="Build all architectures (x64, x86, arm64)")
    parser.add_argument("--target", type=str, help="Specific Rust target (e.g. x86_64-pc-windows-msvc)")
    args = parser.parse_args()
    
    targets = []
    if args.all:
        targets = [
            ("x86_64-pc-windows-msvc", "x64"),
            ("i686-pc-windows-msvc", "x86"),
            ("aarch64-pc-windows-msvc", "arm64")
        ]
    elif args.target:
        # Determine arch folder from target name
        t = args.target.lower()
        if "x86_64" in t:
            arch = "x64"
        elif "i686" in t:
            arch = "x86"
        elif "aarch64" in t or "arm64" in t:
            arch = "arm64"
        else:
            print(f"Unknown target architecture: {args.target}")
            sys.exit(1)
        targets = [(args.target, arch)]
    else:
        # Build host architecture by default
        arch, target = get_host_arch()
        print(f"No target specified. Building for host architecture: {arch} ({target})")
        targets = [(target, arch)]
        
    failed_targets = []
    for target, arch in targets:
        try:
            compile_and_copy(target, arch)
        except Exception as e:
            print(f"Error building for target {target}: {e}")
            failed_targets.append(target)
            if not args.all:
                sys.exit(1)
                
    if failed_targets:
        print(f"\nERROR: Failed to compile for the following targets: {', '.join(failed_targets)}")
        print("Packaging aborted. Please ensure you have the required compiler toolchains installed.")
        sys.exit(1)
                
    # Run packaging
    print("Packaging add-on...")
    package()

if __name__ == "__main__":
    main()
