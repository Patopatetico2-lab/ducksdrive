"""
ducksdrive/system/power_monitor.py - System sleep/resume D-Bus monitor for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Listens to org.freedesktop.login1.Manager PrepareForSleep(bool) via QtDBus
so DucksDrive can proactively verify and reconnect FUSE mounts after wake-up.
"""

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class SystemPowerMonitor(QObject):
    """Monitors Linux logind PrepareForSleep D-Bus signals safely with graceful fallback."""

    system_sleeping = Signal()
    system_resumed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._connected = False
        self._connect_dbus()

    def _connect_dbus(self) -> None:
        """Subscribes to org.freedesktop.login1 PrepareForSleep(bool) on the system bus if available."""
        try:
            from PySide6.QtCore import SLOT
            from PySide6.QtDBus import QDBusConnection

            bus = QDBusConnection.systemBus()
            if not bus.isConnected():
                logger.debug("System D-Bus unavailable; sleep/wake auto-reconnect signal disabled.")
                return
            ok = bus.connect(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                self,
                SLOT("on_prepare_for_sleep(bool)"),
            )
            self._connected = bool(ok)
            if self._connected:
                logger.info("Connected to org.freedesktop.login1 PrepareForSleep D-Bus signal.")
            else:
                logger.debug("Could not connect to PrepareForSleep D-Bus signal.")
        except Exception as exc:
            logger.debug("SystemPowerMonitor D-Bus initialization skipped: %s", exc)

    @Slot(bool)
    def on_prepare_for_sleep(self, sleeping: bool) -> None:
        """Handles PrepareForSleep(true/false) transitions emitted by systemd-logind."""
        if sleeping:
            logger.info("System entering sleep/suspend state.")
            self.system_sleeping.emit()
        else:
            logger.info("System resumed from sleep/suspend state.")
            self.system_resumed.emit()
