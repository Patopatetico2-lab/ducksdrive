"""
rclone_settings.py - Configuration manager for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Manages loading and saving user preferences in ~/.config/ducksdrive/config.json
with safe default values and automatic system autostart synchronization.
"""

import json
import logging
import os
import re
import shutil
import socket
import sys
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)

xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_DIR = os.path.join(xdg_config, "ducksdrive")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

_allocated_ports: Set[int] = set()


def allocate_rc_port(host: str = "127.0.0.1", preferred_port: int = 5572) -> int:
    """Finds a free port on host not currently in _allocated_ports, marks it allocated, and returns it."""
    port = preferred_port
    while port < 65535:
        if port not in _allocated_ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                    _allocated_ports.add(port)
                    return port
                except OSError:
                    pass
        port += 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        allocated = int(s.getsockname()[1])
        _allocated_ports.add(allocated)
        return allocated


def release_rc_port(port: int) -> None:
    """Releases a previously allocated port so it can be reused."""
    _allocated_ports.discard(port)


def get_available_rc_port(host: str = "127.0.0.1", preferred_port: int = 5572) -> int:
    """Checks if preferred_port is free on host and not allocated; if in use, selects an available localhost port."""
    port = preferred_port
    while port < 65535:
        if port not in _allocated_ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                    return port
                except OSError:
                    pass
        port += 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


DEFAULT_CONFIG: Dict[str, Any] = {
    "bwlimit": "off",
    "vfs_cache_max_size": "2G",
    "transfers": 4,
    "vfs_cache_mode": "full",
    "autostart": True,
    "language": "pt_BR",
    "last_remote": "",
    "theme": "auto",
    "remotes_config": {},
    "use_system_keyring": False,
    "rc_use_unix_socket": True,
}

DEFAULT_REMOTE_CONFIG: Dict[str, Any] = {
    "custom_mount_point": "",
    "mount_path": "",
    "read_only": False,
    "custom_flags": "",
    "bwlimit": "",
    "vfs_cache_max_size": "",
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
    """Calculates total disk space in bytes currently used by the rclone VFS cache using fast os.scandir traversal."""
    cache_dir = get_rclone_cache_dir()
    if not os.path.isdir(cache_dir):
        return 0
    total = 0
    stack = [cache_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
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


SYSTEM_DEFAULT_PALETTE: Any = None
_SYSTEM_DARK_CACHE: bool | None = None


def set_system_default_palette(palette: Any) -> None:
    """Stores a copy of the original system QPalette captured at application startup."""
    global SYSTEM_DEFAULT_PALETTE, _SYSTEM_DARK_CACHE
    try:
        from PySide6.QtGui import QPalette

        SYSTEM_DEFAULT_PALETTE = QPalette(palette)
        win_col = SYSTEM_DEFAULT_PALETTE.color(QPalette.Window)
        if win_col.isValid():
            _SYSTEM_DARK_CACHE = win_col.lightness() < 128
    except Exception:
        SYSTEM_DEFAULT_PALETTE = palette


def _detect_system_dark() -> bool:
    """Detects whether the desktop environment is using a dark color scheme before custom palettes are applied."""
    global _SYSTEM_DARK_CACHE
    if _SYSTEM_DARK_CACHE is not None:
        return _SYSTEM_DARK_CACHE

    # 1. Check saved system default palette or initial QApplication palette
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication, QPalette
        from PySide6.QtWidgets import QApplication

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorScheme") and hasattr(Qt, "ColorScheme"):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                _SYSTEM_DARK_CACHE = True
                return True
            if scheme == Qt.ColorScheme.Light:
                _SYSTEM_DARK_CACHE = False
                return False

        if SYSTEM_DEFAULT_PALETTE is not None:
            win_col = SYSTEM_DEFAULT_PALETTE.color(QPalette.Window)
            if win_col.isValid() and win_col.lightness() < 128:
                _SYSTEM_DARK_CACHE = True
                return True

        app = QApplication.instance()
        if app is not None:
            window_color = app.palette().color(QPalette.Window)
            if window_color.isValid() and window_color.lightness() < 128:
                _SYSTEM_DARK_CACHE = True
                return True
    except Exception:
        pass

    # 2. Check KDE kdeglobals
    kdeglobals = os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "kdeglobals",
    )
    if os.path.exists(kdeglobals):
        try:
            with open(kdeglobals, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"^(?:ColorScheme|Name|LookAndFeelPackage)\s*=\s*.*dark", content, re.IGNORECASE | re.MULTILINE):
                _SYSTEM_DARK_CACHE = True
                return True
            m = re.search(r"^\s*BackgroundNormal\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", content, re.MULTILINE)
            if m:
                r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                _SYSTEM_DARK_CACHE = lum < 128
                return _SYSTEM_DARK_CACHE
            if re.search(r"^(?:ColorScheme|Name|LookAndFeelPackage)\s*=\s*.*(?:light|breeze)", content, re.IGNORECASE | re.MULTILINE):
                _SYSTEM_DARK_CACHE = False
                return False
        except Exception:
            pass

    if "dark" in os.environ.get("GTK_THEME", "").lower():
        _SYSTEM_DARK_CACHE = True
        return True

    _SYSTEM_DARK_CACHE = False
    return False


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
    return _detect_system_dark()


def apply_app_theme(theme_mode: str = "auto") -> Any:
    """
    Applies the chosen theme ('light', 'dark', or 'auto') natively via QPalette
    on QApplication (with zero QSS on controls) and updates StatusPopup.
    """
    mode = (theme_mode or load_config().get("theme") or "auto").lower()

    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication, QWidget

        app = QApplication.instance()
        if app is None:
            return None

        if SYSTEM_DEFAULT_PALETTE is None:
            set_system_default_palette(app.palette())

        if mode == "light":
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#eff0f1"))
            palette.setColor(QPalette.WindowText, QColor("#232629"))
            palette.setColor(QPalette.Base, QColor("#ffffff"))
            palette.setColor(QPalette.AlternateBase, QColor("#f7f7f7"))
            palette.setColor(QPalette.Text, QColor("#232629"))
            palette.setColor(QPalette.Button, QColor("#fcfcfc"))
            palette.setColor(QPalette.ButtonText, QColor("#232629"))
            palette.setColor(QPalette.Mid, QColor("#bdc3c7"))
            palette.setColor(QPalette.Dark, QColor("#7f8c8d"))
            palette.setColor(QPalette.Light, QColor("#ffffff"))
            palette.setColor(QPalette.Highlight, QColor("#3daee9"))
            palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
            palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
            palette.setColor(QPalette.ToolTipText, QColor("#232629"))
            palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#7f8c8d"))
            palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#7f8c8d"))
            palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#7f8c8d"))
            app.setPalette(palette)
        elif mode == "dark":
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#202326"))
            palette.setColor(QPalette.WindowText, QColor("#eff0f1"))
            palette.setColor(QPalette.Base, QColor("#181a1c"))
            palette.setColor(QPalette.AlternateBase, QColor("#232629"))
            palette.setColor(QPalette.Text, QColor("#eff0f1"))
            palette.setColor(QPalette.Button, QColor("#292c30"))
            palette.setColor(QPalette.ButtonText, QColor("#eff0f1"))
            palette.setColor(QPalette.Mid, QColor("#3b4045"))
            palette.setColor(QPalette.Dark, QColor("#121415"))
            palette.setColor(QPalette.Light, QColor("#454a4f"))
            palette.setColor(QPalette.Highlight, QColor("#3daee9"))
            palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
            palette.setColor(QPalette.ToolTipBase, QColor("#202326"))
            palette.setColor(QPalette.ToolTipText, QColor("#eff0f1"))
            palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#6c737a"))
            palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#6c737a"))
            palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6c737a"))
            app.setPalette(palette)
        else:
            is_dark = _detect_system_dark()
            sys_is_dark = SYSTEM_DEFAULT_PALETTE.color(QPalette.Window).lightness() < 128
            if is_dark and not sys_is_dark:
                palette = QPalette()
                palette.setColor(QPalette.Window, QColor("#202326"))
                palette.setColor(QPalette.WindowText, QColor("#eff0f1"))
                palette.setColor(QPalette.Base, QColor("#181a1c"))
                palette.setColor(QPalette.AlternateBase, QColor("#232629"))
                palette.setColor(QPalette.Text, QColor("#eff0f1"))
                palette.setColor(QPalette.Button, QColor("#292c30"))
                palette.setColor(QPalette.ButtonText, QColor("#eff0f1"))
                palette.setColor(QPalette.Mid, QColor("#3b4045"))
                palette.setColor(QPalette.Dark, QColor("#121415"))
                palette.setColor(QPalette.Light, QColor("#454a4f"))
                palette.setColor(QPalette.Highlight, QColor("#3daee9"))
                palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
                palette.setColor(QPalette.ToolTipBase, QColor("#202326"))
                palette.setColor(QPalette.ToolTipText, QColor("#eff0f1"))
                palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#6c737a"))
                palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#6c737a"))
                palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6c737a"))
                app.setPalette(palette)
            else:
                palette = QPalette(SYSTEM_DEFAULT_PALETTE)
                app.setPalette(SYSTEM_DEFAULT_PALETTE)

        for top in app.topLevelWidgets():
            if hasattr(top, "_applied_theme_state") and hasattr(top, "apply_theme"):
                top.apply_theme(mode)
            else:
                top.setPalette(palette)
                for child in top.findChildren(QWidget):
                    child.setPalette(palette)
                top.style().unpolish(top)
                top.style().polish(top)
                top.update()

        return palette
    except Exception as exc:
        logger.debug("Failed to apply global theme: %s", exc)
        return None


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
        is_flatpak = os.path.exists("/.flatpak-info") or bool(os.environ.get("FLATPAK_ID"))

        if is_flatpak:
            flatpak_id = os.environ.get("FLATPAK_ID") or "com.github.patopatetico2.ducksdrive"
            exec_cmd = f"flatpak run {flatpak_id}"
            icon_path = flatpak_id
        elif appimage_path and os.path.exists(appimage_path):
            exec_cmd = f'"{appimage_path}"'
            icon_path = os.path.join(local_dir, "icon.svg")
            if not os.path.exists(icon_path):
                bundled_icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.svg")
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
            exec_cmd = f'"{bin_path}"'
            icon_path = os.path.join(local_dir, "icon.svg")
            if not os.path.exists(icon_path):
                icon_path = "ducksdrive"
        elif os.path.exists("/usr/bin/ducksdrive"):
            exec_cmd = '"/usr/bin/ducksdrive"'
            icon_path = "ducksdrive"
        else:
            bin_path = shutil.which("ducksdrive") or user_bin
            exec_cmd = f'"{bin_path}"'
            icon_path = "ducksdrive"

        content = f"""[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec={exec_cmd}
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


def get_remote_config(remote_name: str) -> Dict[str, Any]:
    """
    Returns the custom per-remote configuration for the given cloud remote.
    Merges with DEFAULT_REMOTE_CONFIG defaults.
    """
    clean = remote_name.rstrip(":") if remote_name else ""
    cfg = load_config()
    remotes_cfg = cfg.get("remotes_config")
    if not isinstance(remotes_cfg, dict):
        remotes_cfg = {}
    specific = remotes_cfg.get(clean)
    res = DEFAULT_REMOTE_CONFIG.copy()
    if isinstance(specific, dict):
        res.update(specific)
    if res.get("custom_mount_point") and not res.get("mount_path"):
        res["mount_path"] = res["custom_mount_point"]
    elif res.get("mount_path") and not res.get("custom_mount_point"):
        res["custom_mount_point"] = res["mount_path"]
    return res


def save_remote_config(remote_name: str, remote_cfg: Dict[str, Any]) -> bool:
    """
    Persists the custom per-remote configuration for the given cloud remote into config.json.
    """
    clean = remote_name.rstrip(":") if remote_name else ""
    if not clean:
        return False
    cfg = load_config()
    if "remotes_config" not in cfg or not isinstance(cfg["remotes_config"], dict):
        cfg["remotes_config"] = {}
    merged = DEFAULT_REMOTE_CONFIG.copy()
    merged.update(remote_cfg)
    if merged.get("mount_path") and not merged.get("custom_mount_point"):
        merged["custom_mount_point"] = merged["mount_path"]
    elif merged.get("custom_mount_point") and not merged.get("mount_path"):
        merged["mount_path"] = merged["custom_mount_point"]
    cfg["remotes_config"][clean] = merged
    return save_config(cfg)

