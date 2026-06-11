import os
import sys
import ctypes
from ctypes import wintypes
import zipfile
import tempfile
import uuid
import base64
import zlib
import threading
import time
import json
import tones
import ui
import wx
from logHandler import log

# Windows API constants
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

# Load DLLs
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

class DROPFILES(ctypes.Structure):
    _fields_ = (
        ('pFiles', wintypes.DWORD),  # Offset to the start of the file list
        ('pt', wintypes.POINT),      # Drop point
        ('fNC', wintypes.BOOL),      # Non-client area flag
        ('fWide', wintypes.BOOL),    # Wide character (Unicode) flag
    )

def get_files_from_clipboard():
    """Retrieve absolute file paths from the Windows Clipboard using CF_HDROP."""
    files = []
    if user32.OpenClipboard(None):
        try:
            h_hdrop = user32.GetClipboardData(CF_HDROP)
            if h_hdrop:
                file_count = shell32.DragQueryFileW(h_hdrop, -1, None, 0)
                for i in range(file_count):
                    path_len = shell32.DragQueryFileW(h_hdrop, i, None, 0)
                    buffer = ctypes.create_unicode_buffer(path_len + 1)
                    shell32.DragQueryFileW(h_hdrop, i, buffer, path_len + 1)
                    files.append(buffer.value)
        except Exception as e:
            log.error(f"P2P File Transfer: Error reading clipboard: {e}")
        finally:
            user32.CloseClipboard()
    return files

def set_clipboard_hdrop_and_move(file_paths):
    """Set the clipboard to files/folders and instruct target application to Cut (MOVE)."""
    # 1. Prepare files data (UTF-16LE, double null terminated)
    files_data = b"".join([p.encode('utf-16le') + b'\x00\x00' for p in file_paths]) + b'\x00\x00'
    hdrop_size = ctypes.sizeof(DROPFILES) + len(files_data)
    
    # Allocate global memory for CF_HDROP
    h_hdrop = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, hdrop_size)
    p_hdrop = kernel32.GlobalLock(h_hdrop)
    
    df = DROPFILES()
    df.pFiles = ctypes.sizeof(DROPFILES)
    df.fWide = True
    
    ctypes.memmove(p_hdrop, ctypes.byref(df), ctypes.sizeof(DROPFILES))
    ctypes.memmove(p_hdrop + ctypes.sizeof(DROPFILES), files_data, len(files_data))
    kernel32.GlobalUnlock(h_hdrop)
    
    # 2. Prepare Preferred DropEffect (DROPEFFECT_MOVE = 2 for Cut/Move semantics)
    drop_effect = ctypes.c_ulong(2)
    h_effect = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, ctypes.sizeof(drop_effect))
    p_effect = kernel32.GlobalLock(h_effect)
    ctypes.memmove(p_effect, ctypes.byref(drop_effect), ctypes.sizeof(drop_effect))
    kernel32.GlobalUnlock(h_effect)
    
    # 3. Open clipboard, empty it, and set both formats
    success = False
    if user32.OpenClipboard(None):
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_HDROP, h_hdrop)
            CF_PREFERRED_DROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")
            user32.SetClipboardData(CF_PREFERRED_DROPEFFECT, h_effect)
            success = True
        except Exception as e:
            log.error(f"P2P File Transfer: Error writing clipboard: {e}")
        finally:
            user32.CloseClipboard()
    return success

class FileSenderThread(threading.Thread):
    def __init__(self, paths, send_fn):
        super().__init__()
        self.paths = paths
        self.send_fn = send_fn
        self.daemon = True
        self.cancelled = False
        self.bytes_sent = 0
        self.bytes_acked = 0

    def handle_ack(self, num_bytes):
        self.bytes_acked += num_bytes

    def run(self):
        try:
            # Determine if we should package as zip (if multiple files or a directory)
            is_zip = len(self.paths) > 1 or os.path.isdir(self.paths[0])
            source_path = None
            filename = None

            if is_zip:
                wx.CallAfter(ui.message, "Packaging files...")
                # Count files to show zip progress
                total_files = 0
                for path in self.paths:
                    if os.path.isdir(path):
                        for root, dirs, files in os.walk(path):
                            total_files += len(files)
                    else:
                        total_files += 1

                temp_zip = os.path.join(tempfile.gettempdir(), f"nvda_remote_{uuid.uuid4().hex}.zip")
                files_zipped = 0
                last_reported = 0

                with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_STORED) as zf:
                    for path in self.paths:
                        if self.cancelled:
                            break
                        if os.path.isdir(path):
                            base_dir = os.path.dirname(path)
                            for root, dirs, files in os.walk(path):
                                for file in files:
                                    if self.cancelled:
                                        break
                                    file_path = os.path.join(root, file)
                                    arcname = os.path.relpath(file_path, base_dir)
                                    zf.write(file_path, arcname)
                                    files_zipped += 1
                                    
                                    percent = int((files_zipped / total_files) * 100)
                                    if percent // 10 > last_reported // 10:
                                        last_reported = percent
                                        wx.CallAfter(ui.message, f"Packaging: {percent}%")
                        else:
                            zf.write(path, os.path.basename(path))
                            files_zipped += 1

                if self.cancelled:
                    if os.path.exists(temp_zip):
                        os.remove(temp_zip)
                    return

                source_path = temp_zip
                filename = "clipboard_package.zip"
            else:
                source_path = self.paths[0]
                filename = os.path.basename(source_path)

            file_size = os.path.getsize(source_path)
            wx.CallAfter(ui.message, f"Sending {filename} ({file_size / (1024*1024):.1f} MB)...")

            # Send start message
            start_payload = {
                "type": "p2p_file_start",
                "filename": filename,
                "size": file_size,
                "is_zip": is_zip
            }
            self.send_fn(json.dumps(start_payload))

            # Send chunks with stream compression
            chunk_size = 262144 # 256KB
            self.bytes_sent = 0
            self.bytes_acked = 0
            last_reported_percent = 0

            with open(source_path, "rb") as f:
                while self.bytes_sent < file_size and not self.cancelled:
                    # Flow control: wait if we have sent more than 1MB ahead of the receiver's disk writes
                    while self.bytes_sent - self.bytes_acked > 1024 * 1024 and not self.cancelled:
                        time.sleep(0.01)

                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Compress chunk using zlib (level 1 = fastest)
                    compressed = zlib.compress(chunk, 1)
                    encoded = base64.b64encode(compressed).decode('utf-8')

                    chunk_payload = {
                        "type": "p2p_file_chunk",
                        "data": encoded
                    }
                    self.send_fn(json.dumps(chunk_payload))

                    self.bytes_sent += len(chunk)
                    percent = int((self.bytes_sent / file_size) * 100)
                    if percent // 10 > last_reported_percent // 10:
                        last_reported_percent = percent
                        wx.CallAfter(ui.message, f"Uploading: {percent}%")
                        # Play a short tick sound
                        wx.CallAfter(tones.playTone, 440, 30)

            # Cleanup temp zip if we created one
            if is_zip and os.path.exists(source_path):
                os.remove(source_path)

            if self.cancelled:
                self.send_fn(json.dumps({"type": "p2p_file_abort"}))
                wx.CallAfter(ui.message, "File transfer cancelled.")
                return

            # Send end message
            self.send_fn(json.dumps({"type": "p2p_file_end"}))
            wx.CallAfter(ui.message, "File transfer complete.")
            # Play success chime
            wx.CallAfter(tones.playTone, 523, 100)
            time.sleep(0.1)
            wx.CallAfter(tones.playTone, 659, 150)

        except Exception as e:
            log.error(f"P2P File Transfer: Error in sender thread: {e}")
            self.send_fn(json.dumps({"type": "p2p_file_abort"}))
            wx.CallAfter(ui.message, "File transfer failed.")


class FileReceiver:
    def __init__(self):
        self.filename = None
        self.size = 0
        self.is_zip = False
        self.temp_filepath = None
        self.file_handle = None
        self.bytes_received = 0
        self.last_reported_percent = 0

    def start(self, filename, size, is_zip):
        self.filename = filename
        self.size = size
        self.is_zip = is_zip
        self.bytes_received = 0
        self.last_reported_percent = 0
        
        # Open temp file directly to write chunks to disk (zero RAM bloat)
        self.temp_filepath = os.path.join(tempfile.gettempdir(), f"nvda_recv_{uuid.uuid4().hex}.tmp")
        self.file_handle = open(self.temp_filepath, "wb")
        
        wx.CallAfter(ui.message, f"Receiving file: {filename} ({size / (1024*1024):.1f} MB)...")

    def write_chunk(self, encoded_data):
        if not self.file_handle:
            return
        
        # Base64 decode and zlib decompress
        compressed = base64.b64decode(encoded_data)
        chunk = zlib.decompress(compressed)
        
        self.file_handle.write(chunk)
        self.bytes_received += len(chunk)
        
        # Report progress
        if self.size > 0:
            percent = int((self.bytes_received / self.size) * 100)
            if percent // 10 > self.last_reported_percent // 10:
                self.last_reported_percent = percent
                wx.CallAfter(ui.message, f"Downloading: {percent}%")
                wx.CallAfter(tones.playTone, 550, 30)

        return len(chunk)

    def finalize(self):
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        if not self.temp_filepath or not os.path.exists(self.temp_filepath):
            return

        final_paths = []
        try:
            if self.is_zip:
                wx.CallAfter(ui.message, "Extracting files...")
                extract_dir = os.path.join(tempfile.gettempdir(), f"nvda_ext_{uuid.uuid4().hex}")
                os.makedirs(extract_dir, exist_ok=True)
                
                with zipfile.ZipFile(self.temp_filepath, 'r') as zf:
                    zf.extractall(extract_dir)
                
                # Cleanup temp zip file
                os.remove(self.temp_filepath)
                
                # Gather top level items in extracted dir
                final_paths = [os.path.join(extract_dir, name) for name in os.listdir(extract_dir)]
            else:
                # Rename the temp file to the original filename in temp folder
                dest_path = os.path.join(tempfile.gettempdir(), self.filename)
                # Overwrite if exists
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                os.rename(self.temp_filepath, dest_path)
                final_paths = [dest_path]

            # Write to clipboard with Cut/Move effect
            if set_clipboard_hdrop_and_move(final_paths):
                wx.CallAfter(ui.message, "File ready. Press Control V to paste.")
                # Play success chime
                wx.CallAfter(tones.playTone, 659, 100)
                time.sleep(0.1)
                wx.CallAfter(tones.playTone, 523, 150)
            else:
                wx.CallAfter(ui.message, "Failed to write files to clipboard.")
        except Exception as e:
            log.error(f"P2P File Transfer: Error finalizing received file: {e}")
            wx.CallAfter(ui.message, "File extraction failed.")
            self.cleanup()

    def abort(self):
        self.cleanup()
        wx.CallAfter(ui.message, "File transfer aborted.")

    def cleanup(self):
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None
        if self.temp_filepath and os.path.exists(self.temp_filepath):
            try:
                os.remove(self.temp_filepath)
            except Exception:
                pass
            self.temp_filepath = None
