"""
app_config.py - Configuration manager for DucksDrive.

Handles reading and writing the JSON configuration file located at
~/.config/ducksdrive/config.json with safe defaults.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.expanduser("~/.config/ducksdrive")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "bwlimit": "off",
    "vfs_cache_max_size": "2G",
    "transfers": 4,
    "vfs_cache_mode": "full",  # Fixed for data safety, not editable in UI
    "autostart": False
}


def load_config() -> dict:
    """
    Loads configuration from disk. If the file doesn't exist or is invalid,
    it returns the default configuration and ensures the file is created.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        logger.info("Configuration file not found. Creating with defaults.")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            
        # Ensure all default keys exist (merging defaults for backward compatibility)
        updated_config = DEFAULT_CONFIG.copy()
        updated_config.update(config)
        return updated_config
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load config: {e}. Falling back to defaults.")
        return DEFAULT_CONFIG.copy()


def save_config(config_data: dict) -> bool:
    """
    Saves the provided configuration dictionary to disk.
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
        logger.info(f"Configuration saved to {CONFIG_FILE}")
        return True
    except IOError as e:
        logger.error(f"Failed to save config: {e}")
        return False
