"""
notifications.py - Native desktop notification manager for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Dispatches desktop notifications via D-Bus org.freedesktop.Notifications
or the notify-send CLI with graceful fallback to QSystemTrayIcon.showMessage().
"""

import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Optional

from i18n import tr

logger = logging.getLogger(__name__)


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
    Sends a desktop notification. Tries D-Bus org.freedesktop.Notifications first,
    then notify-send, then falls back to tray.showMessage().

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

    # 1. Try D-Bus org.freedesktop.Notifications
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage

        bus = QDBusConnection.sessionBus()
        if bus.isConnected():
            msg = QDBusMessage.createMethodCall(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
            )
            urgency_levels = {"low": 0, "normal": 1, "critical": 2}
            hints = {"urgency": urgency_levels.get(urgency, 1)}
            msg.setArguments([
                "DucksDrive",
                0,
                active_icon,
                title,
                message,
                [],
                hints,
                5000,
            ])
            reply = bus.call(msg, timeout=1200)
            if reply.type() != QDBusMessage.MessageType.ErrorMessage:
                logger.debug("Desktop notification sent via D-Bus: %s", title)
                return True
    except Exception as exc:
        logger.debug("D-Bus notification failed: %s", exc)

    # 2. Try notify-send command
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
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            logger.debug("Desktop notification dispatched via notify-send: %s", title)
            return True
        except Exception as exc:
            logger.debug("notify-send execution failed: %s", exc)

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
