"""
rclone_settings.py - Configuration manager for DucksDrive.

Manages loading and saving user preferences in ~/.config/ducksdrive/config.json
with safe default values.
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.expanduser("~/.config/ducksdrive")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "bwlimit": "off",
    "vfs_cache_max_size": "2G",
    "transfers": 4,
    "vfs_cache_mode": "full",
    "autostart": True,
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
    Saves configuration dictionary to ~/.config/ducksdrive/config.json.
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        logger.info("Saved configuration successfully to %s", CONFIG_FILE)
        return True
    except Exception as e:
        logger.error("Failed to save config file: %s", e)
        return False
