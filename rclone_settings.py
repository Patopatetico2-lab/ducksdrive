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


_SYSTEM_DARK_CACHE: bool | None = None


def _detect_system_dark() -> bool:
    """Detects whether the desktop environment is using a dark color scheme before custom palettes are applied."""
    global _SYSTEM_DARK_CACHE
    # 1. Check KDE kdeglobals first (unaffected by QApplication palette overrides)
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
            if "breezelight" in content or "breeze" in content:
                return False
        except Exception:
            pass

    # 2. Check Qt 6.5+ native colorScheme hint or initial palette
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

        if _SYSTEM_DARK_CACHE is None:
            app = QApplication.instance()
            if app is not None:
                window_color = app.palette().color(QPalette.Window)
                if window_color.isValid():
                    _SYSTEM_DARK_CACHE = window_color.lightness() < 128
        if _SYSTEM_DARK_CACHE is not None:
            return _SYSTEM_DARK_CACHE
    except Exception:
        pass

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


def get_theme_stylesheet(theme_pref: str | None = None) -> str:
    """
    Returns the centralized QSS stylesheet for all application dialogs, menus, and controls
    according to the resolved theme (Light or Dark).
    """
    is_dark = is_dark_theme_active(theme_pref)
    if is_dark:
        bg = "#202326"
        surface = "#262a2e"
        input_bg = "#1b1e20"
        fg = "#fcfcfc"
        muted_fg = "#7f8c8d"
        border = "#3b4045"
        btn_bg = "#2d3238"
        btn_hover = "#3a4047"
        disabled_bg = "#25292e"
        disabled_fg = "#6c737a"
        accent = "#3daee9"
    else:
        bg = "#ffffff"
        surface = "#f5f5f5"
        input_bg = "#ffffff"
        fg = "#222222"
        muted_fg = "#777777"
        border = "#dcdde1"
        btn_bg = "#f5f5f5"
        btn_hover = "#e8e8e8"
        disabled_bg = "#eeeeee"
        disabled_fg = "#888888"
        accent = "#3498db"

    return f"""
        QDialog, QMessageBox, QProgressDialog {{
            background-color: {bg};
            color: {fg};
        }}
        QDialog QWidget, QMessageBox QWidget, QProgressDialog QWidget {{
            color: {fg};
        }}
        QLabel {{
            background-color: transparent;
            color: {fg};
        }}
        QGroupBox {{
            background-color: {surface};
            color: {fg};
            border: 1px solid {border};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
            font-weight: bold;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 4px;
            color: {fg};
            background-color: transparent;
        }}
        QLineEdit, QSpinBox, QComboBox {{
            background-color: {input_bg};
            color: {fg};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 4px 8px;
            selection-background-color: {accent};
            selection-color: #ffffff;
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
            border: 1px solid {accent};
        }}
        QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
            background-color: {disabled_bg};
            color: {disabled_fg};
            border: 1px solid {border};
        }}
        QComboBox QAbstractItemView {{
            background-color: {input_bg};
            color: {fg};
            border: 1px solid {border};
            selection-background-color: {accent};
            selection-color: #ffffff;
            outline: none;
        }}
        QCheckBox {{
            background-color: transparent;
            color: {fg};
            spacing: 6px;
        }}
        QPushButton {{
            background-color: {btn_bg};
            color: {fg};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 6px 14px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover};
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_fg};
        }}
        QMenu {{
            background-color: {bg};
            color: {fg};
            border: 1px solid {border};
            padding: 4px;
        }}
        QMenu::item {{
            background-color: transparent;
            color: {fg};
            padding: 6px 24px 6px 12px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {accent};
            color: #ffffff;
        }}
        QMenu::item:disabled {{
            color: {muted_fg};
            background-color: transparent;
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {border};
            margin: 4px 6px;
        }}
    """


def apply_app_theme(theme_pref: str | None = None, widget: Any = None) -> bool:
    """
    Applies the chosen theme ('light', 'dark', or 'auto') globally to QApplication,
    the optional target widget, and all currently open top-level windows/menus.
    """
    _detect_system_dark()
    is_dark = is_dark_theme_active(theme_pref)
    qss = get_theme_stylesheet(theme_pref)

    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            pal = QPalette()
            if is_dark:
                win_col = QColor("#202326")
                base_col = QColor("#1b1e20")
                alt_col = QColor("#262a2e")
                text_col = QColor("#fcfcfc")
                btn_col = QColor("#2d3238")
                dis_col = QColor("#6c737a")
                hl_col = QColor("#3daee9")
            else:
                win_col = QColor("#ffffff")
                base_col = QColor("#ffffff")
                alt_col = QColor("#f5f5f5")
                text_col = QColor("#222222")
                btn_col = QColor("#f5f5f5")
                dis_col = QColor("#888888")
                hl_col = QColor("#3498db")

            pal.setColor(QPalette.Window, win_col)
            pal.setColor(QPalette.WindowText, text_col)
            pal.setColor(QPalette.Base, base_col)
            pal.setColor(QPalette.AlternateBase, alt_col)
            pal.setColor(QPalette.ToolTipBase, win_col)
            pal.setColor(QPalette.ToolTipText, text_col)
            pal.setColor(QPalette.Text, text_col)
            pal.setColor(QPalette.Button, btn_col)
            pal.setColor(QPalette.ButtonText, text_col)
            pal.setColor(QPalette.Highlight, hl_col)
            pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
            pal.setColor(QPalette.Disabled, QPalette.Text, dis_col)
            pal.setColor(QPalette.Disabled, QPalette.WindowText, dis_col)
            pal.setColor(QPalette.Disabled, QPalette.ButtonText, dis_col)
            app.setPalette(pal)
            app.setStyleSheet(qss)

            if widget is not None and not hasattr(widget, "_applied_theme_state"):
                widget.setStyleSheet(qss)

            for top in app.topLevelWidgets():
                if top is widget:
                    continue
                if hasattr(top, "apply_theme") and callable(top.apply_theme):
                    try:
                        top.apply_theme(theme_pref)
                    except TypeError:
                        top.apply_theme()
                elif not hasattr(top, "_applied_theme_state"):
                    top.setStyleSheet(qss)
    except Exception as exc:
        logger.debug("Failed to apply global theme: %s", exc)

    return is_dark


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
