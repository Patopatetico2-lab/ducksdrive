"""
ducksdrive/core/rclone_settings.py - Configuration manager for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Manages loading and saving user preferences in ~/.config/ducksdrive/config.json
with safe default values and automatic system autostart synchronization.
"""

import copy
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
from typing import Any, Dict, List, Set

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
    "vfs_cache_max_age": "24h",
    "vfs_read_chunk_streams": 4,
    "performance_profile": "balanced",
    "transfers": 4,
    "vfs_cache_mode": "full",
    "autostart": True,
    "auto_reconnect": True,
    "language": "pt_BR",
    "last_remote": "",
    "last_active_remotes": [],
    "theme": "auto",
    "remotes_config": {},
    "use_system_keyring": False,
    "rc_use_unix_socket": True,
}

DEFAULT_REMOTE_CONFIG: Dict[str, Any] = {
    "custom_mount_point": "",
    "mount_path": "",
    "read_only": False,
    "auto_mount": False,
    "custom_flags": "",
    "bwlimit": "",
    "vfs_cache_max_size": "",
    "performance_profile": "",
}

PERFORMANCE_PROFILE_PRESETS: tuple[tuple[str, str], ...] = (
    ("Equilibrado (Dolphin Rápido + Multi-Stream)", "balanced"),
    ("Downloads Grandes e Streaming (Buffer 32M / Read-Ahead 64M)", "streaming"),
    ("Upload Seguro (Evita Saturar a Rede / Bufferbloat)", "safe_upload"),
)

BANDWIDTH_LIMIT_PRESETS: tuple[tuple[str, str], ...] = (
    ("Ilimitado (Sem teto de banda)", "off"),
    ("Upload 2 MB/s • Download Ilimitado", "2M:off"),
    ("Upload 4 MB/s • Download Ilimitado (Anti-Lag)", "4M:off"),
    ("Upload 10 MB/s • Download Ilimitado", "10M:off"),
    ("Upload 25 MB/s • Download Ilimitado", "25M:off"),
    ("1 MB/s (Econômico)", "1M"),
    ("2 MB/s (Modesto)", "2M"),
    ("5 MB/s (Equilibrado)", "5M"),
    ("10 MB/s (Rápido)", "10M"),
    ("25 MB/s (Muito Rápido)", "25M"),
    ("50 MB/s (Máximo)", "50M"),
    ("100 MB/s (Ultra Rápido)", "100M"),
)

QUICK_BANDWIDTH_PRESETS: tuple[tuple[str, str], ...] = (
    ("Ilimitado", "off"),
    ("↑ 2 MB/s • ↓ Ilimitado", "2M:off"),
    ("↑ 4 MB/s • ↓ Ilimitado", "4M:off"),
    ("↑ 10 MB/s • ↓ Ilimitado", "10M:off"),
    ("↑ 25 MB/s • ↓ Ilimitado", "25M:off"),
    ("1 MB/s", "1M"),
    ("2 MB/s", "2M"),
    ("5 MB/s", "5M"),
    ("10 MB/s", "10M"),
    ("25 MB/s", "25M"),
    ("50 MB/s", "50M"),
    ("100 MB/s", "100M"),
)


_CONFIG_CACHE: Dict[str, Any] | None = None
_CONFIG_MTIME: float | None = None


def _atomic_write_file(target_path: str, content: str) -> None:
    """Atomically writes text content to target_path via a temp file + os.replace."""
    parent = os.path.dirname(target_path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ducksdrive-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def load_config() -> Dict[str, Any]:
    """
    Loads configuration from ~/.config/ducksdrive/config.json.
    Merges with defaults if missing keys, returning an isolated deep copy.
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime: float | None = os.path.getmtime(CONFIG_FILE)
    except OSError:
        mtime = None

    if _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
        return copy.deepcopy(_CONFIG_CACHE)

    config = copy.deepcopy(DEFAULT_CONFIG)
    if mtime is not None:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    config.update(data)
        except Exception as e:
            logger.error("Failed to load config file: %s. Using defaults.", e)

    _CONFIG_CACHE = copy.deepcopy(config)
    _CONFIG_MTIME = mtime
    return config


def save_config(config: Dict[str, Any]) -> bool:
    """
    Atomically saves configuration dictionary to ~/.config/ducksdrive/config.json
    and synchronizes system autostart (.desktop file).
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        _atomic_write_file(CONFIG_FILE, json.dumps(config, indent=4) + "\n")
        try:
            _CONFIG_MTIME = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _CONFIG_MTIME = None
        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged.update(copy.deepcopy(config))
        _CONFIG_CACHE = merged
        logger.info("Saved configuration successfully to %s", CONFIG_FILE)

        # Synchronize system autostart state
        _sync_autostart(bool(config.get("autostart", True)))
        return True
    except Exception as e:
        logger.error("Failed to save config file: %s", e)
        return False


def save_last_remote(remote_name: str) -> None:
    """Atomically persists the most recently mounted remote name in ~/.config/ducksdrive/config.json."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    clean = remote_name.rstrip(":") if remote_name else ""
    config = load_config()
    if config.get("last_remote") == clean:
        return
    config["last_remote"] = clean
    try:
        _atomic_write_file(CONFIG_FILE, json.dumps(config, indent=4) + "\n")
        try:
            _CONFIG_MTIME = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _CONFIG_MTIME = None
        _CONFIG_CACHE = copy.deepcopy(config)
        logger.info("Updated last_remote to '%s'", clean)
    except Exception as e:
        logger.error("Failed to save last_remote: %s", e)


def save_active_remotes(remote_names: List[str]) -> None:
    """Atomically persists the list of currently active/mounted remote names."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    cleaned: List[str] = []
    for name in remote_names:
        c = name.rstrip(":") if name else ""
        if c and c not in cleaned:
            cleaned.append(c)

    config = load_config()
    if config.get("last_active_remotes") == cleaned:
        return
    config["last_active_remotes"] = cleaned
    try:
        _atomic_write_file(CONFIG_FILE, json.dumps(config, indent=4) + "\n")
        try:
            _CONFIG_MTIME = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _CONFIG_MTIME = None
        _CONFIG_CACHE = copy.deepcopy(config)
        logger.info("Updated last_active_remotes to %s", cleaned)
    except Exception as e:
        logger.error("Failed to save last_active_remotes: %s", e)


def get_auto_mount_remotes(available_remotes: List[str]) -> List[str]:
    """
    Determines which configured cloud remotes should auto-mount at startup:
    1. Remotes explicitly marked `auto_mount=True` in `remotes_config`.
    2. Otherwise `last_active_remotes` from the previous session (multi-mount memory).
    3. Fallback to `last_remote`, or the first available remote.
    """
    available = [r.rstrip(":") for r in available_remotes if r and r.rstrip(":")]
    if not available:
        return []
    available_set = set(available)
    cfg = load_config()

    remotes_cfg = cfg.get("remotes_config")
    explicit: List[str] = []
    if isinstance(remotes_cfg, dict):
        for r in available:
            r_cfg = remotes_cfg.get(r)
            if isinstance(r_cfg, dict) and r_cfg.get("auto_mount"):
                explicit.append(r)
    if explicit:
        return explicit

    last_active = cfg.get("last_active_remotes")
    remembered: List[str] = []
    if isinstance(last_active, list):
        for item in last_active:
            c = str(item).rstrip(":")
            if c in available_set and c not in remembered:
                remembered.append(c)
    if remembered:
        return remembered

    last_remote = str(cfg.get("last_remote") or "").rstrip(":")
    if last_remote in available_set:
        return [last_remote]
    return [available[0]]


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
    global SYSTEM_DEFAULT_PALETTE
    try:
        from PySide6.QtGui import QPalette

        SYSTEM_DEFAULT_PALETTE = QPalette(palette)
    except Exception:
        SYSTEM_DEFAULT_PALETTE = palette


def _detect_system_dark() -> bool:
    """
    Detects whether the desktop environment is using a dark color scheme.
    Prioritizes Qt6 desktop colorScheme hints, KDE kdeglobals, and GTK_THEME before
    falling back to Qt's default palette (which is often light in pip-installed PySide6).
    """
    # 1. Qt 6.5+ desktop portal / platform colorScheme hint
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorScheme") and hasattr(Qt, "ColorScheme"):
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
    except Exception:
        pass

    # 2. Check KDE kdeglobals (works even when pip PySide6 lacks plasma-integration)
    kdeglobals = os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "kdeglobals",
    )
    if os.path.exists(kdeglobals):
        try:
            with open(kdeglobals, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"^(?:ColorScheme|Name|LookAndFeelPackage)\s*=\s*.*dark", content, re.IGNORECASE | re.MULTILINE):
                return True
            m = re.search(r"^\s*BackgroundNormal\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", content, re.MULTILINE)
            if m:
                r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                return lum < 128
            if re.search(r"^(?:ColorScheme|Name|LookAndFeelPackage)\s*=\s*.*(?:light|breeze)", content, re.IGNORECASE | re.MULTILINE):
                return False
        except Exception:
            pass

    # 3. Check GTK_THEME environment variable
    if "dark" in os.environ.get("GTK_THEME", "").lower():
        return True

    # 4. Fallback to saved system default palette / QApplication palette
    try:
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication

        if SYSTEM_DEFAULT_PALETTE is not None:
            win_col = SYSTEM_DEFAULT_PALETTE.color(QPalette.ColorRole.Window)
            if win_col.isValid() and win_col.lightness() < 128:
                return True

        app = QApplication.instance()
        if isinstance(app, QApplication):
            window_color = app.palette().color(QPalette.ColorRole.Window)
            if window_color.isValid() and window_color.lightness() < 128:
                return True
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


def apply_app_theme(theme_mode: str = "auto") -> Any:
    """
    Applies the chosen theme ('light', 'dark', or 'auto') natively via QPalette
    on QApplication (with zero QSS on controls) and updates StatusPopup.
    """
    mode = (theme_mode or load_config().get("theme") or "auto").lower()

    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication, QWidget

        def _build_dark_palette() -> QPalette:
            p = QPalette()
            p.setColor(QPalette.ColorRole.Window, QColor("#202326"))
            p.setColor(QPalette.ColorRole.WindowText, QColor("#eff0f1"))
            p.setColor(QPalette.ColorRole.Base, QColor("#181a1c"))
            p.setColor(QPalette.ColorRole.AlternateBase, QColor("#232629"))
            p.setColor(QPalette.ColorRole.Text, QColor("#eff0f1"))
            p.setColor(QPalette.ColorRole.Button, QColor("#292c30"))
            p.setColor(QPalette.ColorRole.ButtonText, QColor("#eff0f1"))
            p.setColor(QPalette.ColorRole.Mid, QColor("#3b4045"))
            p.setColor(QPalette.ColorRole.Dark, QColor("#121415"))
            p.setColor(QPalette.ColorRole.Light, QColor("#454a4f"))
            p.setColor(QPalette.ColorRole.Highlight, QColor("#3daee9"))
            p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#202326"))
            p.setColor(QPalette.ColorRole.ToolTipText, QColor("#eff0f1"))
            p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#6c737a"))
            p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6c737a"))
            p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6c737a"))
            return p

        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return None

        if SYSTEM_DEFAULT_PALETTE is None:
            set_system_default_palette(app.palette())

        if mode == "light":
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#eff0f1"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#232629"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7f7f7"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#232629"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#fcfcfc"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#232629"))
            palette.setColor(QPalette.ColorRole.Mid, QColor("#bdc3c7"))
            palette.setColor(QPalette.ColorRole.Dark, QColor("#7f8c8d"))
            palette.setColor(QPalette.ColorRole.Light, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#3daee9"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#232629"))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#7f8c8d"))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#7f8c8d"))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#7f8c8d"))
            app.setPalette(palette)
        elif mode == "dark":
            palette = _build_dark_palette()
            app.setPalette(palette)
        else:
            is_dark = _detect_system_dark()
            sys_is_dark = SYSTEM_DEFAULT_PALETTE.color(QPalette.ColorRole.Window).lightness() < 128
            if is_dark and not sys_is_dark:
                palette = _build_dark_palette()
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
                style_obj = top.style()
                if style_obj is not None:
                    style_obj.unpolish(top)
                    style_obj.polish(top)
                top.update()

        return palette
    except Exception as exc:
        logger.debug("Failed to apply global theme: %s", exc)
        return None


_DESKTOP_EXEC_SPECIAL = set(' \t\n"\'\\><~|&;$*?#()`')


def _format_desktop_arg(arg: str) -> str:
    """Formats an Exec= argument per the FreeDesktop Desktop Entry Specification."""
    if not arg:
        return '""'
    if not any(ch in _DESKTOP_EXEC_SPECIAL for ch in arg):
        return arg
    escaped = (
        arg.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def _is_executable_file(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _resolve_autostart_command() -> tuple[str, str, str]:
    """
    Determines the exact (exec_cmd, try_exec, icon_path) for the currently running installation.
    Prioritizes the active runtime environment (Flatpak, AppImage, system package, user install,
    or source checkout) so autostart never points to a missing or stale launcher.
    """
    from ducksdrive.resources import get_icon_path, get_install_root, get_package_dir

    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    xdg_bin = os.environ.get("XDG_BIN_HOME", os.path.expanduser("~/.local/bin"))
    local_dir = os.path.join(xdg_data, "ducksdrive")
    local_bin = os.path.join(local_dir, "ducksdrive")
    user_bin = os.path.join(xdg_bin, "ducksdrive")
    appimage_path = os.environ.get("APPIMAGE")
    is_flatpak = os.path.exists("/.flatpak-info") or bool(os.environ.get("FLATPAK_ID"))
    pkg_dir = get_package_dir()
    install_root = get_install_root()

    if is_flatpak:
        flatpak_id = os.environ.get("FLATPAK_ID") or "com.github.patopatetico2.ducksdrive"
        return f"flatpak run {flatpak_id} --autostart", "flatpak", flatpak_id

    if appimage_path and os.path.isfile(appimage_path):
        icon_path = os.path.join(local_dir, "icon.svg")
        if not os.path.exists(icon_path):
            bundled_icon = get_icon_path()
            if bundled_icon and os.path.exists(bundled_icon):
                try:
                    os.makedirs(local_dir, exist_ok=True)
                    shutil.copy2(bundled_icon, icon_path)
                except Exception:
                    icon_path = "ducksdrive"
            else:
                icon_path = "ducksdrive"
        return f"{_format_desktop_arg(appimage_path)} --autostart", appimage_path, icon_path

    # Running from a system package (/usr/share/ducksdrive)
    if os.path.realpath(install_root).startswith("/usr/share/ducksdrive") and _is_executable_file("/usr/bin/ducksdrive"):
        return "/usr/bin/ducksdrive --autostart", "/usr/bin/ducksdrive", "ducksdrive"

    # Running from a user installation (~/.local/share/ducksdrive)
    if os.path.realpath(install_root) == os.path.realpath(local_dir):
        bin_path = local_bin if _is_executable_file(local_bin) else user_bin
        if _is_executable_file(bin_path):
            icon_path = os.path.join(local_dir, "icon.svg")
            if not os.path.exists(icon_path):
                icon_path = "ducksdrive"
            return f"{_format_desktop_arg(bin_path)} --autostart", bin_path, icon_path

    # Prefer an installed executable if present, otherwise invoke the package entrypoint directly
    for candidate, icon_candidate in (
        (user_bin, os.path.join(local_dir, "icon.svg")),
        ("/usr/bin/ducksdrive", "ducksdrive"),
        (shutil.which("ducksdrive") or "", "ducksdrive"),
    ):
        if _is_executable_file(candidate):
            icon_path = icon_candidate if (icon_candidate == "ducksdrive" or os.path.exists(icon_candidate)) else "ducksdrive"
            return f"{_format_desktop_arg(candidate)} --autostart", candidate, icon_path

    entry_py = os.path.join(pkg_dir, "__main__.py")
    py_exe = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    icon_path = get_icon_path() or "ducksdrive"
    return (
        f"{_format_desktop_arg(py_exe)} {_format_desktop_arg(entry_py)} --autostart",
        py_exe,
        icon_path,
    )


def _sync_autostart(enabled: bool) -> None:
    """Creates or removes the XDG autostart .desktop file (host-aware for Flatpak, KDE Plasma 6, GNOME, XFCE)."""
    is_flatpak = os.path.exists("/.flatpak-info") or bool(os.environ.get("FLATPAK_ID"))
    if is_flatpak:
        # Inside Flatpak XDG_CONFIG_HOME is sandboxed (~/.var/app/...); write to host ~/.config/autostart
        autostart_dir = os.path.join(os.path.expanduser("~"), ".config", "autostart")
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        autostart_dir = os.path.join(xdg_config, "autostart")
    autostart_file = os.path.join(autostart_dir, "ducksdrive.desktop")

    if enabled:
        os.makedirs(autostart_dir, exist_ok=True)
        exec_cmd, try_exec, icon_path = _resolve_autostart_command()

        content = (
            "[Desktop Entry]\n"
            "Name=DucksDrive\n"
            "Comment=Manage cloud drives with Rclone\n"
            f"TryExec={try_exec}\n"
            f"Exec={exec_cmd}\n"
            f"Icon={icon_path}\n"
            "Terminal=false\n"
            "Type=Application\n"
            "Categories=Network;Utility;\n"
            "StartupNotify=false\n"
            "Hidden=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "X-GNOME-Autostart-Delay=5\n"
            "X-KDE-autostart-after=panel\n"
        )
        try:
            if os.path.exists(autostart_file):
                try:
                    with open(autostart_file, "r", encoding="utf-8") as f:
                        if f.read() == content:
                            return
                except OSError:
                    pass
            _atomic_write_file(autostart_file, content)
            logger.info("Synchronized autostart: Enabled (%s).", exec_cmd)
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
    res = copy.deepcopy(DEFAULT_REMOTE_CONFIG)
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
    merged = copy.deepcopy(DEFAULT_REMOTE_CONFIG)
    merged.update(remote_cfg)
    if merged.get("mount_path") and not merged.get("custom_mount_point"):
        merged["custom_mount_point"] = merged["mount_path"]
    elif merged.get("custom_mount_point") and not merged.get("mount_path"):
        merged["mount_path"] = merged["custom_mount_point"]
    cfg["remotes_config"][clean] = merged
    return save_config(cfg)


def delete_remote_config(remote_name: str) -> None:
    """Removes per-remote custom configuration from config.json when a cloud remote is deleted."""
    clean = remote_name.rstrip(":") if remote_name else ""
    if not clean:
        return
    cfg = load_config()
    changed = False
    remotes_cfg = cfg.get("remotes_config")
    if isinstance(remotes_cfg, dict) and clean in remotes_cfg:
        del remotes_cfg[clean]
        changed = True
    last_active = cfg.get("last_active_remotes")
    if isinstance(last_active, list) and clean in last_active:
        cfg["last_active_remotes"] = [r for r in last_active if r != clean]
        changed = True
    if changed:
        save_config(cfg)

