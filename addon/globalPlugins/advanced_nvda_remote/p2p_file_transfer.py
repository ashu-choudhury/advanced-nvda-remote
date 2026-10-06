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

# Configure ctypes signatures for 64-bit/32-bit Windows compatibility
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_int

user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p

user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p

user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_int

user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_int

user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
user32.RegisterClipboardFormatW.restype = ctypes.c_uint

kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p

kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p

kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p

kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_int

shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
shell32.DragQueryFileW.restype = ctypes.c_uint

class DROPFILES(ctypes.Structure):
    _fields_ = (
        ('pFiles', wintypes.DWORD),  # Offset to the start of the file list
        ('pt', wintypes.POINT),      # Drop point
        ('fNC', wintypes.BOOL),      # Non-client area flag
        ('fWide', wintypes.BOOL),    # Wide character (Unicode) flag
    )

def is_safe_relpath(base_dir, target_path):
    """Check that target_path stays strictly within base_dir (Zip Slip defense)."""
    base_abs = os.path.abspath(base_dir)
    target_abs = os.path.abspath(target_path)
    return target_abs == base_abs or target_abs.startswith(base_abs + os.sep)

def sanitize_filename(filename):
    """Strip directory traversal, drive letters, and dangerous characters."""
    if not filename:
        return f"file_{uuid.uuid4().hex[:8]}.dat"
    # Take only the basename
    cleaned = os.path.basename(filename)
    # Strip forbidden Windows filename characters: < > : " / \ | ? *
    cleaned = "".join(c for c in cleaned if c not in '<>:"/\\|?*').strip(". ")
    if not cleaned:
        return f"file_{uuid.uuid4().hex[:8]}.dat"
    return cleaned

def get_files_from_clipboard():
    """Retrieve absolute file paths from the Windows Clipboard using CF_HDROP."""
    files = []
    # Retry briefly in case another process temporarily holds clipboard
    for _ in range(5):
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
                break
            except Exception as e:
                log.error(f"P2P File Transfer: Error reading clipboard: {e}")
                break
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return files

def set_clipboard_hdrop_and_move(file_paths):
    """Set the clipboard to files/folders and instruct target application to Cut (MOVE)."""
    if not file_paths:
        return False

    # 1. Prepare files data (UTF-16LE, double null terminated)
    files_data = b"".join([p.encode('utf-16le') + b'\x00\x00' for p in file_paths]) + b'\x00\x00'
    hdrop_size = ctypes.sizeof(DROPFILES) + len(files_data)
    
    # Allocate global memory for CF_HDROP
    h_hdrop = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, hdrop_size)
    if not h_hdrop:
        log.error("P2P File Transfer: GlobalAlloc failed for CF_HDROP")
        return False
        
    p_hdrop = kernel32.GlobalLock(h_hdrop)
    if not p_hdrop:
        kernel32.GlobalFree(h_hdrop)
        return False

    df = DROPFILES()
    df.pFiles = ctypes.sizeof(DROPFILES)
    df.fWide = True
    
    ctypes.memmove(p_hdrop, ctypes.byref(df), ctypes.sizeof(DROPFILES))
    ctypes.memmove(p_hdrop + ctypes.sizeof(DROPFILES), files_data, len(files_data))
    kernel32.GlobalUnlock(h_hdrop)
    
    # 2. Prepare Preferred DropEffect (DROPEFFECT_MOVE = 2 for Cut/Move semantics)
    drop_effect = ctypes.c_ulong(2)
    h_effect = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, ctypes.sizeof(drop_effect))
    if not h_effect:
        kernel32.GlobalFree(h_hdrop)
        log.error("P2P File Transfer: GlobalAlloc failed for DropEffect")
        return False

    p_effect = kernel32.GlobalLock(h_effect)
    if not p_effect:
        kernel32.GlobalFree(h_hdrop)
        kernel32.GlobalFree(h_effect)
        return False

    ctypes.memmove(p_effect, ctypes.byref(drop_effect), ctypes.sizeof(drop_effect))
    kernel32.GlobalUnlock(h_effect)
    
    # 3. Open clipboard, empty it, and set both formats
    success = False
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                if user32.SetClipboardData(CF_HDROP, h_hdrop):
                    CF_PREFERRED_DROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")
                    user32.SetClipboardData(CF_PREFERRED_DROPEFFECT, h_effect)
                    success = True
                break
            except Exception as e:
                log.error(f"P2P File Transfer: Error writing clipboard: {e}")
                break
            finally:
                user32.CloseClipboard()
        time.sleep(0.03)

    # Prevent Win32 GlobalAlloc memory leak if clipboard open failed:
    if not success:
        log.warn("P2P File Transfer: Could not open clipboard. Freeing allocated memory.")
        kernel32.GlobalFree(h_hdrop)
        kernel32.GlobalFree(h_effect)

    return success

class FileSenderThread(threading.Thread):
    def __init__(self, paths, send_fn, send_chunk_fn, get_buffered_amount_fn):
        super().__init__()
        self.paths = paths
        self.send_fn = send_fn
        self.send_chunk_fn = send_chunk_fn
        self.get_buffered_amount_fn = get_buffered_amount_fn
        self.daemon = True
        self.cancelled = False
        self.bytes_sent = 0
        self.bytes_acked = 0

    def handle_ack(self, num_bytes):
        self.bytes_acked += num_bytes

    def run(self):
        source_path = None
        is_zip = False
        try:
            is_zip = len(self.paths) > 1 or os.path.isdir(self.paths[0])
            filename = None

            if is_zip:
                wx.CallAfter(ui.message, "Packaging files...")
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
                                    if total_files > 0:
                                        percent = int((files_zipped / total_files) * 100)
                                        if percent // 10 > last_reported // 10:
                                            last_reported = percent
                                            wx.CallAfter(ui.message, f"Packaging: {percent}%")
                        else:
                            zf.write(path, os.path.basename(path))
                            files_zipped += 1

                if self.cancelled:
                    if os.path.exists(temp_zip):
                        try:
                            os.remove(temp_zip)
                        except Exception:
                            pass
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

            chunk_size = 32768  # 32KB chunk
            self.bytes_sent = 0
            self.bytes_acked = 0
            last_reported_percent = 0
            chunk_count = 0

            with open(source_path, "rb") as f:
                while self.bytes_sent < file_size and not self.cancelled:
                    window_excess = self.bytes_sent - self.bytes_acked > 1024 * 1024
                    buffer_excess = False
                    if not window_excess and chunk_count % 16 == 0:
                        buffer_excess = self.get_buffered_amount_fn() > 524288
                        
                    if window_excess or buffer_excess:
                        while not self.cancelled:
                            window_excess = self.bytes_sent - self.bytes_acked > 1024 * 1024
                            if not window_excess:
                                if self.get_buffered_amount_fn() <= 524288:
                                    break
                            time.sleep(0.02)

                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    compressed = zlib.compress(chunk, 1)
                    self.send_chunk_fn(compressed)

                    self.bytes_sent += len(chunk)
                    chunk_count += 1
                    if file_size > 0:
                        percent = int((self.bytes_sent / file_size) * 100)
                        if percent // 10 > last_reported_percent // 10:
                            last_reported_percent = percent
                            wx.CallAfter(ui.message, f"Uploading: {percent}%")
                            wx.CallAfter(tones.beep, 440, 30)

            if is_zip and source_path and os.path.exists(source_path):
                try:
                    os.remove(source_path)
                except Exception:
                    pass

            if self.cancelled:
                self.send_fn(json.dumps({"type": "p2p_file_abort"}))
                wx.CallAfter(ui.message, "File transfer cancelled.")
                return

            self.send_fn(json.dumps({"type": "p2p_file_end"}))
            wx.CallAfter(ui.message, "File transfer complete.")
            wx.CallAfter(tones.beep, 523, 100)
            time.sleep(0.1)
            wx.CallAfter(tones.beep, 659, 150)

        except Exception as e:
            log.error(f"P2P File Transfer: Error in sender thread: {e}")
            if is_zip and source_path and os.path.exists(source_path):
                try:
                    os.remove(source_path)
                except Exception:
                    pass
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
        self._lock = threading.Lock()

    def start(self, filename, size, is_zip):
        with self._lock:
            self.filename = sanitize_filename(filename)
            self.size = size
            self.is_zip = is_zip
            self.bytes_received = 0
            self.last_reported_percent = 0
            
            # Open temp file directly to write chunks to disk
            self.temp_filepath = os.path.join(tempfile.gettempdir(), f"nvda_recv_{uuid.uuid4().hex}.tmp")
            self.file_handle = open(self.temp_filepath, "wb")
            
            wx.CallAfter(ui.message, f"Receiving file: {self.filename} ({size / (1024*1024):.1f} MB)...")

    def write_chunk(self, data):
        with self._lock:
            if not self.file_handle:
                return 0
            
            if isinstance(data, str):
                compressed = base64.b64decode(data.encode('utf-8') if hasattr(data, 'encode') else data)
            else:
                compressed = data
                
            chunk = zlib.decompress(compressed)
            self.file_handle.write(chunk)
            self.bytes_received += len(chunk)
            
            if self.size > 0:
                percent = int((self.bytes_received / self.size) * 100)
                if percent // 10 > self.last_reported_percent // 10:
                    self.last_reported_percent = percent
                    wx.CallAfter(ui.message, f"Downloading: {percent}%")
                    wx.CallAfter(tones.beep, 550, 30)

            return len(chunk)

    def finalize(self):
        """Asynchronously finalize file receive on a background thread so transport loop never blocks."""
        with self._lock:
            if self.file_handle:
                try:
                    self.file_handle.close()
                except Exception:
                    pass
                self.file_handle = None

            temp_path = self.temp_filepath
            filename = self.filename
            is_zip = self.is_zip

        if not temp_path or not os.path.exists(temp_path):
            return

        # Offload decompression & clipboard writing to a worker thread
        worker = threading.Thread(
            target=self._finalize_worker,
            args=(temp_path, filename, is_zip),
            daemon=True
        )
        worker.start()

    def _finalize_worker(self, temp_path, filename, is_zip):
        final_paths = []
        try:
            if is_zip:
                wx.CallAfter(ui.message, "Extracting files...")
                extract_dir = os.path.join(tempfile.gettempdir(), f"nvda_ext_{uuid.uuid4().hex}")
                os.makedirs(extract_dir, exist_ok=True)
                
                with zipfile.ZipFile(temp_path, 'r') as zf:
                    for member in zf.infolist():
                        # Prevent Zip Slip / path traversal
                        target_path = os.path.join(extract_dir, member.filename)
                        if not is_safe_relpath(extract_dir, target_path):
                            log.error(f"P2P File Transfer: Skipped unsafe zip member: {member.filename}")
                            continue
                        zf.extract(member, extract_dir)
                
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                
                final_paths = [os.path.join(extract_dir, name) for name in os.listdir(extract_dir)]
            else:
                safe_name = sanitize_filename(filename)
                dest_path = os.path.join(tempfile.gettempdir(), safe_name)
                # Overwrite if exists
                if os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                os.rename(temp_path, dest_path)
                final_paths = [dest_path]

            if set_clipboard_hdrop_and_move(final_paths):
                wx.CallAfter(ui.message, "File ready. Press Control V to paste.")
                wx.CallAfter(tones.beep, 659, 100)
                time.sleep(0.1)
                wx.CallAfter(tones.beep, 523, 150)
            else:
                wx.CallAfter(ui.message, "Failed to write files to clipboard.")
        except Exception as e:
            log.error(f"P2P File Transfer: Error finalizing received file: {e}")
            wx.CallAfter(ui.message, "File extraction failed.")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def abort(self):
        self.cleanup()
        wx.CallAfter(ui.message, "File transfer aborted.")

    def cleanup(self):
        with self._lock:
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
