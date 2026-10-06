import os
import sys
import re
import json
import time
import urllib.request
import urllib.error
import tempfile
import threading

try:
    import wx
except ImportError:
    wx = None

try:
    import ui
except ImportError:
    ui = None

try:
    import tones
except ImportError:
    tones = None

try:
    from logHandler import log
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("updater")

GITHUB_REPO = "ashu-choudhury/advanced-nvda-remote"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_SECONDS = 86400  # 24 hours between automated checks

def notify_message(msg):
    if wx and ui:
        wx.CallAfter(ui.message, msg)
    elif ui:
        ui.message(msg)
    else:
        log.info(msg)

def play_tone(freq, dur):
    if wx and tones:
        wx.CallAfter(tones.beep, freq, dur)
    elif tones:
        tones.beep(freq, dur)

def parse_version(v_str):
    """Convert version string like '1.1.0' or 'v1.2.3-beta' into a comparable tuple of integers."""
    if not v_str:
        return (0,)
    v_clean = re.sub(r'^[vV]', '', str(v_str).strip())
    digits = [int(m) for m in re.findall(r'\d+', v_clean)]
    return tuple(digits) if digits else (0,)

def is_newer_version(remote_v, local_v):
    """Return True if remote_v is strictly newer than local_v."""
    r_parts = parse_version(remote_v)
    l_parts = parse_version(local_v)
    max_len = max(len(r_parts), len(l_parts))
    r_pad = r_parts + (0,) * (max_len - len(r_parts))
    l_pad = l_parts + (0,) * (max_len - len(l_parts))
    return r_pad > l_pad

def get_current_version():
    """Retrieve current add-on version from NVDA addonHandler or manifest.ini."""
    try:
        import addonHandler
        for addon in addonHandler.getAvailableAddons():
            if addon.name == "advanced-nvda-remote":
                return str(addon.version).strip()
    except Exception:
        pass

    try:
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        manifest_path = os.path.join(os.path.dirname(os.path.dirname(cur_dir)), "manifest.ini")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("version"):
                        return line.split("=")[1].strip().strip('"\'')
    except Exception:
        pass

    return "1.1.0"

def get_config_file():
    try:
        import globalVars
        if hasattr(globalVars, "appArgs") and hasattr(globalVars.appArgs, "configPath") and globalVars.appArgs.configPath:
            conf_dir = os.path.join(globalVars.appArgs.configPath, "advanced_nvda_remote")
            os.makedirs(conf_dir, exist_ok=True)
            return os.path.join(conf_dir, "updater.json")
    except Exception:
        pass
    return os.path.join(tempfile.gettempdir(), "advanced_nvda_remote_updater.json")

def read_config():
    conf_path = get_config_file()
    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_check": 0, "auto_check": True}

def save_config(cfg):
    conf_path = get_config_file()
    try:
        with open(conf_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception as e:
        log.warn(f"P2P Updater: Could not save updater config: {e}")

def is_native_update_pending():
    """Check if NVDA's native Addon Store / Addon Updater has already staged an action for this add-on."""
    try:
        import addonHandler
        for addon in addonHandler.getAvailableAddons():
            if addon.name == "advanced-nvda-remote":
                if getattr(addon, "isPendingRemove", False) or getattr(addon, "isPendingInstall", False):
                    return True
    except Exception:
        pass
    return False

class UpdateChecker:
    def __init__(self):
        self._check_thread = None

    def start_background_check(self, delay=6.0):
        """Start automated check in the background after NVDA finishes booting."""
        cfg = read_config()
        if not cfg.get("auto_check", True):
            log.info("P2P Updater: Automatic update checks disabled by config.")
            return

        now = time.time()
        last_check = cfg.get("last_check", 0)
        if now - last_check < CHECK_INTERVAL_SECONDS:
            log.info("P2P Updater: Skipping auto-check (checked within last 24h).")
            return

        def _delayed_run():
            time.sleep(delay)
            self.check_now(manual=False)

        t = threading.Thread(target=_delayed_run, daemon=True, name="AdvancedNvdaRemoteUpdateCheck")
        t.start()

    def check_now(self, manual=False):
        """Query GitHub Releases API for updates."""
        if self._check_thread and self._check_thread.is_alive():
            if manual:
                notify_message("Update check is already in progress...")
            return

        t = threading.Thread(target=self._run_check, args=(manual,), daemon=True, name="AdvancedNvdaRemoteUpdateWorker")
        self._check_thread = t
        t.start()

    def _run_check(self, manual):
        if manual:
            notify_message("Checking for Advanced NVDA Remote updates...")

        if is_native_update_pending():
            log.info("P2P Updater: NVDA native updater already has an update or removal pending. Skipping.")
            if manual:
                notify_message("An update is already pending installation in NVDA.")
            return

        current_ver = get_current_version()
        try:
            req = urllib.request.Request(
                API_URL,
                headers={
                    "User-Agent": "Advanced-NVDA-Remote-Updater",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))

            cfg = read_config()
            cfg["last_check"] = time.time()
            save_config(cfg)

            tag_name = data.get("tag_name", "")
            remote_ver = tag_name.lstrip("vV")
            release_name = data.get("name") or tag_name
            release_notes = data.get("body") or ""
            assets = data.get("assets", [])

            addon_asset = None
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(".nvda-addon"):
                    addon_asset = asset
                    break

            if not addon_asset:
                log.info(f"P2P Updater: Latest release {tag_name} has no .nvda-addon asset.")
                if manual:
                    notify_message(f"Advanced NVDA Remote is up to date (version {current_ver}).")
                return

            download_url = addon_asset.get("browser_download_url")
            asset_size = addon_asset.get("size", 0)

            if is_newer_version(remote_ver, current_ver):
                update_info = {
                    "current_version": current_ver,
                    "version": remote_ver,
                    "title": release_name,
                    "notes": release_notes,
                    "download_url": download_url,
                    "size": asset_size,
                }
                if wx:
                    wx.CallAfter(self._prompt_user_update, update_info)
                else:
                    self._prompt_user_update(update_info)
            else:
                log.info(f"P2P Updater: Add-on is up to date (current: {current_ver}, remote: {remote_ver}).")
                if manual:
                    notify_message(f"Advanced NVDA Remote is up to date (version {current_ver}).")

        except urllib.error.HTTPError as e:
            log.warn(f"P2P Updater: GitHub API HTTP error: {e.code} {e.reason}")
            if manual:
                notify_message(f"Could not check for updates (HTTP {e.code}).")
        except Exception as e:
            log.error(f"P2P Updater: Failed to check for updates: {e}")
            if manual:
                notify_message("Failed to connect to the update server.")

    def _prompt_user_update(self, info):
        try:
            import gui
            version = info["version"]
            cur_version = info["current_version"]
            title = info["title"]
            notes = info["notes"].strip()

            msg_lines = [
                f"A new version of Advanced NVDA Remote ({version}) is available!\n",
                f"Current version: {cur_version}",
            ]
            if title and title != version:
                msg_lines.append(f"Title: {title}")
            if notes:
                clean_notes = notes[:600] + ("\n... [visit GitHub for full notes]" if len(notes) > 600 else "")
                msg_lines.append(f"\nRelease Notes:\n{clean_notes}")

            msg_lines.append("\nWould you like to download and install this update now?")
            prompt_text = "\n".join(msg_lines)

            res = gui.messageBox(
                message=prompt_text,
                caption="Advanced NVDA Remote Update Available",
                style=wx.YES_NO | wx.ICON_QUESTION
            )

            if res == wx.YES:
                t = threading.Thread(target=self._download_and_install, args=(info,), daemon=True, name="AdvancedNvdaRemoteDownloader")
                t.start()
        except Exception as e:
            log.error(f"P2P Updater: Error showing update prompt: {e}")

    def _download_and_install(self, info):
        download_url = info["download_url"]
        version = info["version"]
        
        notify_message(f"Downloading Advanced NVDA Remote {version}...")
        play_tone(440, 50)

        temp_path = os.path.join(tempfile.gettempdir(), f"advanced_nvda_remote_{version}_{uuid_hex()}.nvda-addon")
        try:
            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "Advanced-NVDA-Remote-Updater"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_path, "wb") as out_f:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_percent = 0

                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = int((downloaded / total_size) * 100)
                        if pct // 25 > last_percent // 25:
                            last_percent = pct
                            notify_message(f"Downloading: {pct}%")

            if wx:
                wx.CallAfter(self._install_addon_bundle, temp_path, version)
            else:
                self._install_addon_bundle(temp_path, version)

        except Exception as e:
            log.error(f"P2P Updater: Error downloading update: {e}")
            notify_message("Download failed.")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def _install_addon_bundle(self, bundle_path, version):
        try:
            import gui
            import addonHandler
            if is_native_update_pending():
                notify_message("An update is already pending. Please restart NVDA first.")
                return

            log.info(f"P2P Updater: Installing bundle {bundle_path}...")
            bundle = addonHandler.AddonBundle(bundle_path)
            addonHandler.installAddonBundle(bundle)

            # Cleanup temp file
            try:
                os.remove(bundle_path)
            except Exception:
                pass

            play_tone(659, 100)
            res = gui.messageBox(
                message=f"Advanced NVDA Remote {version} has been installed successfully!\n\nNVDA must be restarted for the update to take effect.\n\nWould you like to restart NVDA now?",
                caption="Update Installed Successfully",
                style=wx.YES_NO | wx.ICON_INFORMATION
            )
            if res == wx.YES:
                import core
                core.restart()

        except Exception as e:
            log.error(f"P2P Updater: Error installing add-on: {e}")
            try:
                import gui
                gui.messageBox(
                    message=f"Failed to install add-on update: {e}",
                    caption="Update Error",
                    style=wx.OK | wx.ICON_ERROR
                )
            except Exception:
                pass

def uuid_hex():
    import uuid
    return uuid.uuid4().hex[:8]

# Global singleton instance
updater_instance = UpdateChecker()
