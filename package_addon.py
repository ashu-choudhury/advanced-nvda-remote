import zipfile
import os

addon_dir = "addon"
output_path = "advanced-nvda-remote.nvda-addon"

def package():
    # Remove existing output if it exists
    if os.path.exists(output_path):
        os.remove(output_path)
        
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(addon_dir):
            for file in files:
                # Do not package temporary files
                if file.endswith('.pyc') or '__pycache__' in root:
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, addon_dir)
                zipf.write(file_path, arcname)
    print(f"Successfully packaged add-on to {output_path}")

if __name__ == "__main__":
    package()
