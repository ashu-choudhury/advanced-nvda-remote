import zipfile
import os

addon_dir = "addon"
output_path = "advanced-nvda-remote.nvda-addon"
fallback_path = "advanced-nvda-remote-new.nvda-addon"

def package():
    target_path = output_path
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except PermissionError:
        print(f"Warning: {output_path} is locked. Packaging to fallback: {fallback_path}")
        target_path = fallback_path
        if os.path.exists(fallback_path):
            try:
                os.remove(fallback_path)
            except Exception:
                pass
        
    with zipfile.ZipFile(target_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(addon_dir):
            for file in files:
                # Do not package temporary files
                if file.endswith('.pyc') or '__pycache__' in root:
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, addon_dir)
                zipf.write(file_path, arcname)
    print(f"Successfully packaged add-on to {target_path}")

if __name__ == "__main__":
    package()
