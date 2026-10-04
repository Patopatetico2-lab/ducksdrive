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
}


def load_config() -> Dict[str, Any]:
    """
    Loads configuration from ~/.config/ducksdrive/config.json.
    Merges with defaults if missing keys.
    """
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    config.update(data)
        except Exception as e:
            logger.error("Failed to load config file: %s. Using defaults.", e)
    return config


def save_config(config: Dict[str, Any]) -> bool:
    """
    Saves configuration dictionary to ~/.config/ducksdrive/config.json
    and synchronizes system autostart (.desktop file).
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        logger.info("Saved configuration successfully to %s", CONFIG_FILE)
        
        # Synchronize system autostart state
        _sync_autostart(bool(config.get("autostart", True)))
        return True
    except Exception as e:
        logger.error("Failed to save config file: %s", e)
        return False


def _sync_autostart(enabled: bool) -> None:
    """Creates or removes the autostart .desktop file."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    autostart_dir = os.path.join(xdg_config, "autostart")
    autostart_file = os.path.join(autostart_dir, "ducksdrive.desktop")
    
    if enabled:
        os.makedirs(autostart_dir, exist_ok=True)
        bin_path = shutil.which("ducksdrive") or os.path.expanduser("~/.local/bin/ducksdrive")
        if not os.path.exists(bin_path):
            bin_path = os.path.expanduser("~/.local/bin/ducksdrive")
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        icon_path = os.path.join(xdg_data, "ducksdrive", "icon.svg")
        
        content = f"""[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec={bin_path}
Icon={icon_path}
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
"""
        try:
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
