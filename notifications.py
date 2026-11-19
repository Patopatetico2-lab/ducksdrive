"""
notifications.py - Native desktop notification manager for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Dispatches desktop notifications via D-Bus org.freedesktop.Notifications
or the notify-send CLI with graceful fallback to QSystemTrayIcon.showMessage().
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, List, Optional

from i18n import tr
from rclone_config import get_clean_env

logger = logging.getLogger(__name__)


def _spawn_reaped(cmd: List[str]) -> bool:
    """Spawns a short-lived command with a clean environment and reaps it in a daemon thread (zero zombies)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=get_clean_env(include_keyring=False),
    )

    def _reap(p: subprocess.Popen[bytes]) -> None:
        try:
            p.wait(timeout=10)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=1)
            except Exception:
                pass

    threading.Thread(target=_reap, args=(proc,), name="ducksdrive-notify-reaper", daemon=True).start()
    return True


def _find_notification_icon() -> str:
    """Finds the absolute path to the DucksDrive icon or returns the standard themed name."""
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.svg"),
        os.path.expanduser("~/.local/share/ducksdrive/icon.svg"),
        "/usr/share/ducksdrive/icon.svg",
        "/usr/share/icons/hicolor/scalable/apps/ducksdrive.svg",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return "ducksdrive"


def send_desktop_notification(
    title: str,
    message: str,
    icon: Optional[str] = None,
    urgency: str = "normal",
    fallback_tray: Optional[Any] = None,
) -> bool:
    """
    Sends a desktop notification. Tries `notify-send` first, then `gdbus`
    (`org.freedesktop.Notifications.Notify`), then falls back to `tray.showMessage()`.

    Args:
        title: Notification header summary.
        message: Notification body text.
        icon: Path to icon or themed icon name.
        urgency: Urgency level ('low', 'normal', 'critical').
        fallback_tray: Optional QSystemTrayIcon instance for fallback display.

    Returns:
        bool: True if a notification method was invoked, False otherwise.
    """
    active_icon = icon or _find_notification_icon()

    # 1. Try notify-send command first (standard freedesktop CLI across GNOME, KDE, Wayland, X11)
    notify_send_bin = shutil.which("notify-send")
    if notify_send_bin:
        try:
            cmd = [
                notify_send_bin,
                "-a", "DucksDrive",
                "-u", urgency,
                "-i", active_icon,
                title,
                message,
            ]
            _spawn_reaped(cmd)
            logger.debug("Desktop notification dispatched via notify-send: %s", title)
            return True
        except Exception as exc:
            logger.debug("notify-send execution failed: %s", exc)

    # 2. Try gdbus CLI (calls org.freedesktop.Notifications.Notify directly via D-Bus, non-blocking)
    gdbus_bin = shutil.which("gdbus")
    if gdbus_bin:
        try:
            cmd = [
                gdbus_bin,
                "call",
                "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify",
                "DucksDrive",
                "0",
                json.dumps(active_icon, ensure_ascii=False),
                json.dumps(title, ensure_ascii=False),
                json.dumps(message, ensure_ascii=False),
                "[]",
                "{}",
                "5000",
            ]
            _spawn_reaped(cmd)
            logger.debug("Desktop notification dispatched via gdbus: %s", title)
            return True
        except Exception as exc:
            logger.debug("gdbus execution failed: %s", exc)

    # 3. Fallback to tray.showMessage if available
    if fallback_tray is not None and getattr(fallback_tray, "tray_available", True):
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            icon_type = (
                QSystemTrayIcon.MessageIcon.Critical
                if urgency == "critical"
                else QSystemTrayIcon.MessageIcon.Information
            )
            fallback_tray.showMessage(title, message, icon_type, 5000)
            return True
        except Exception as exc:
            logger.debug("Tray fallback showMessage failed: %s", exc)

    return False


def notify_sync_complete(
    remote_name: str,
    file_count: int = 0,
    fallback_tray: Optional[Any] = None,
) -> bool:
    """Dispatches a notification when background sync transfers complete."""
    clean_name = remote_name.rstrip(":")
    title = tr("Sincronização Concluída")
    if file_count > 0:
        msg = tr("Todos os %d arquivos foram enviados para '%s' com sucesso.") % (file_count, clean_name)
    else:
        msg = tr("Sincronização com '%s' concluída com sucesso.") % clean_name
    return send_desktop_notification(title, msg, urgency="normal", fallback_tray=fallback_tray)


def notify_connection_lost(
    remote_name: str,
    fallback_tray: Optional[Any] = None,
) -> bool:
    """Dispatches a high-priority warning when network drops during transfers."""
    clean_name = remote_name.rstrip(":")
    title = tr("Queda de Conexão")
    msg = tr("A conexão com '%s' foi interrompida durante a transferência. Verifique sua rede.") % clean_name
    return send_desktop_notification(title, msg, urgency="critical", fallback_tray=fallback_tray)


def notify_mount_success(
    remote_name: str,
    mount_point: str,
    fallback_tray: Optional[Any] = None,
) -> bool:
    """Dispatches a confirmation notification when a cloud drive is mounted."""
    clean_name = remote_name.rstrip(":")
    title = tr("Drive Conectado")
    msg = tr("O drive '%s' foi montado em %s.") % (clean_name, mount_point)
    return send_desktop_notification(title, msg, urgency="normal", fallback_tray=fallback_tray)


def notify_info(title: str, message: str, fallback_tray: Optional[Any] = None) -> bool:
    """Dispatches a standard informational desktop notification."""
    return send_desktop_notification(title, message, urgency="normal", fallback_tray=fallback_tray)


def notify_warning(title: str, message: str, fallback_tray: Optional[Any] = None) -> bool:
    """Dispatches a warning desktop notification."""
    return send_desktop_notification(title, message, urgency="normal", fallback_tray=fallback_tray)
