"""
rclone_settings.py - Configuration manager for DucksDrive.

Manages loading and saving user preferences in ~/.config/ducksdrive/config.json
with safe default values and automatic system autostart synchronization.
"""

import json
import logging
import os
import shutil
from typing import Any, Dict

logger = logging.getLogger(__name__)

xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_DIR = os.path.join(xdg_config, "ducksdrive")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "bwlimit": "off",
    "vfs_cache_max_size": "2G",
    "transfers": 4,
    "vfs_cache_mode": "full",
    "autostart": True,
    "language": "pt_BR",
    "last_remote": "",
    "theme": "auto",
}


_CONFIG_CACHE: Dict[str, Any] | None = None
_CONFIG_MTIME: float | None = None


def load_config() -> Dict[str, Any]:
    """
    Loads configuration from ~/.config/ducksdrive/config.json.
    Merges with defaults if missing keys.
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime: float | None = os.path.getmtime(CONFIG_FILE)
    except OSError:
        mtime = None

    if _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
        return _CONFIG_CACHE.copy()

    config = DEFAULT_CONFIG.copy()
    if mtime is not None:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    config.update(data)
        except Exception as e:
            logger.error("Failed to load config file: %s. Using defaults.", e)

    _CONFIG_CACHE = config.copy()
    _CONFIG_MTIME = mtime
    return config


def save_config(config: Dict[str, Any]) -> bool:
    """
    Saves configuration dictionary to ~/.config/ducksdrive/config.json
    and synchronizes system autostart (.desktop file).
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        try:
            _CONFIG_MTIME = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _CONFIG_MTIME = None
        merged = DEFAULT_CONFIG.copy()
        merged.update(config)
        _CONFIG_CACHE = merged
        logger.info("Saved configuration successfully to %s", CONFIG_FILE)
        
        # Synchronize system autostart state
        _sync_autostart(bool(config.get("autostart", True)))
        return True
    except Exception as e:
        logger.error("Failed to save config file: %s", e)
        return False


def save_last_remote(remote_name: str) -> None:
    """Persists the most recently mounted remote name in ~/.config/ducksdrive/config.json."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    clean = remote_name.rstrip(":") if remote_name else ""
    config = load_config()
    if config.get("last_remote") == clean:
        return
    config["last_remote"] = clean
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        try:
            _CONFIG_MTIME = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _CONFIG_MTIME = None
        _CONFIG_CACHE = config.copy()
        logger.info("Updated last_remote to '%s'", clean)
    except Exception as e:
        logger.error("Failed to save last_remote: %s", e)


def get_rclone_cache_dir() -> str:
    """Returns the rclone cache directory path (~/.cache/rclone or XDG_CACHE_HOME/rclone)."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(xdg_cache, "rclone")


def get_vfs_cache_usage_bytes() -> int:
    """Calculates total disk space in bytes currently used by the rclone VFS cache."""
    cache_dir = get_rclone_cache_dir()
    if not os.path.isdir(cache_dir):
        return 0
    total = 0
    for root, _, files in os.walk(cache_dir):
        for name in files:
            fp = os.path.join(root, name)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def clear_vfs_cache_files() -> bool:
    """Removes cached VFS data and metadata directories under ~/.cache/rclone."""
    cache_dir = get_rclone_cache_dir()
    if not os.path.isdir(cache_dir):
        return True
    ok = True
    for sub in ("vfs", "vfsMeta"):
        target = os.path.join(cache_dir, sub)
        if os.path.isdir(target):
            try:
                shutil.rmtree(target)
            except Exception as e:
                logger.warning("Failed to remove VFS cache dir %s: %s", target, e)
                ok = False
    return ok


def is_dark_theme_active(theme_pref: str | None = None) -> bool:
    """
    Determines whether the dark theme should be used based on user config ('auto', 'dark', 'light')
    or the active desktop environment / Qt6 colorScheme / palette when set to 'auto'.
    """
    pref = (theme_pref or load_config().get("theme") or "auto").lower()
    if pref == "dark":
        return True
    if pref == "light":
        return False

    # 1. Check Qt 6.5+ native colorScheme hint first
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication, QPalette
        from PySide6.QtWidgets import QApplication

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorScheme") and hasattr(Qt, "ColorScheme"):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False

        app = QApplication.instance()
        if app is not None:
            window_color = app.palette().color(QPalette.Window)
            if window_color.isValid():
                return window_color.lightness() < 128
    except Exception:
        pass

    # 2. Fallback check in KDE kdeglobals
    kdeglobals = os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "kdeglobals",
    )
    if os.path.exists(kdeglobals):
        try:
            with open(kdeglobals, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
            if "breezedark" in content or "dark" in content:
                return True
        except Exception:
            pass

    return False


def _sync_autostart(enabled: bool) -> None:
    """Creates or removes the autostart .desktop file."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    autostart_dir = os.path.join(xdg_config, "autostart")
    autostart_file = os.path.join(autostart_dir, "ducksdrive.desktop")
    
    if enabled:
        os.makedirs(autostart_dir, exist_ok=True)
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        local_dir = os.path.join(xdg_data, "ducksdrive")
        local_bin = os.path.join(local_dir, "ducksdrive")
        local_venv = os.path.join(local_dir, "venv")
        user_bin = os.path.expanduser("~/.local/bin/ducksdrive")
        appimage_path = os.environ.get("APPIMAGE")

        if appimage_path and os.path.exists(appimage_path):
            bin_path = appimage_path
            icon_path = os.path.join(local_dir, "icon.svg")
            if not os.path.exists(icon_path):
                bundled_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")
                if os.path.exists(bundled_icon):
                    try:
                        os.makedirs(local_dir, exist_ok=True)
                        shutil.copy2(bundled_icon, icon_path)
                    except Exception:
                        icon_path = "ducksdrive"
                else:
                    icon_path = "ducksdrive"
        elif os.path.exists(local_bin) or os.path.exists(local_venv):
            bin_path = local_bin if os.path.exists(local_bin) else user_bin
            icon_path = os.path.join(local_dir, "icon.svg")
            if not os.path.exists(icon_path):
                icon_path = "ducksdrive"
        elif os.path.exists("/usr/bin/ducksdrive"):
            bin_path = "/usr/bin/ducksdrive"
            icon_path = "ducksdrive"
        else:
            bin_path = shutil.which("ducksdrive") or user_bin
            icon_path = "ducksdrive"
        
        content = f"""[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec="{bin_path}"
Icon={icon_path}
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
X-GNOME-Autostart-Delay=3
X-KDE-autostart-after=panel
"""
        try:
            if os.path.exists(autostart_file):
                try:
                    with open(autostart_file, "r", encoding="utf-8") as f:
                        if f.read() == content:
                            return
                except OSError:
                    pass
            with open(autostart_file, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Synchronized autostart: Enabled.")
        except Exception as e:
            logger.error("Failed to enable autostart: %s", e)
    else:
        if os.path.exists(autostart_file):
            try:
                os.remove(autostart_file)
                logger.info("Synchronized autostart: Disabled.")
            except Exception as e:
                logger.error("Failed to disable autostart: %s", e)
