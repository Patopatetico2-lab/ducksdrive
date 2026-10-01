"""
main.py - Entrypoint for DucksDrive.

Orchestrates the application lifecycle:
1. Enforces single instance execution via QLockFile.
2. Configures dual logging (StreamHandler + RotatingFileHandler).
3. Validates system tray availability and rclone version (>= 1.73.5).
4. Initializes the Daemon, Stats Poller, and Tray UI.
5. Manages OS signals (SIGINT/SIGTERM) for graceful FUSE unmount.
"""

import logging
import os
import re
import signal
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from PySide6.QtCore import QObject, Slot, QTimer, QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from rclone_config import list_remote_names
from rclone_daemon import RcloneDaemon
from rclone_stats import RcloneStatsPoller
from filemanager_integration import integrate_mount, clean_integration
from gui_tray import RcloneTrayIcon

# Configure logging (Stream + RotatingFileHandler in ~/.local/share/ducksdrive/ducksdrive.log)
LOG_DIR = os.path.expanduser("~/.local/share/ducksdrive")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "ducksdrive.log")

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), file_handler]
)
logger = logging.getLogger("ducksdrive")

REQUIRED_RCLONE_VERSION = (1, 73, 5)


def get_rclone_version() -> Optional[tuple[int, int, int]]:
    """
    Executes 'rclone version' and parses the version tuple,
    ignoring pre-release or development suffixes (e.g. 1.73.5-beta, 1.74.0-DEV).
    """
    try:
        result = subprocess.run(["rclone", "version"], capture_output=True, text=True, check=True)
        match = re.search(r"rclone v?(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9.-]+)?", result.stdout)
        if match:
            return tuple(map(int, match.groups()))
    except Exception as e:
        logger.error("Could not determine rclone version: %s", e)
    return None


class RcloneAppController(QObject):
    """
    Controller to link background logic (Daemon/Stats) with the UI (Tray).
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.daemon = RcloneDaemon()
        self.tray = RcloneTrayIcon(self.daemon)
        self.stats_poller: Optional[RcloneStatsPoller] = None

        # Connect signals
        self.daemon.state_changed.connect(self._handle_state_change)
        self.tray.request_exit.connect(self.shutdown)

        # Setup OS signal handling for clean FUSE unmount
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Periodically check for OS signals in the Qt loop
        self._signal_timer = QTimer()
        self._signal_timer.timeout.connect(lambda: None)  # Dummy to allow signal processing
        self._signal_timer.start(500)

    def run(self) -> None:
        """
        Starts the application, displays the tray icon, and handles
        first-run configuration (if no remotes exist) or auto-mounts the first remote.
        """
        self.tray.show()

        remotes = list_remote_names()
        if not remotes:
            logger.info("No cloud remotes found. Triggering first-run setup dialog...")
            QTimer.singleShot(200, self.tray._show_new_remote_dialog)
        else:
            logger.info("Auto-mounting first available remote: %s", remotes[0])
            self.daemon.start(remotes[0])

    def _signal_handler(self, sig: int, frame: Any) -> None:
        logger.info("Signal %d received, shutting down...", sig)
        self.shutdown()

    @Slot(str)
    def _handle_state_change(self, state: str) -> None:
        """Handles transitions between mount states."""
        if state == "mounted":
            # Start statistics polling
            if self.daemon.rc_credentials:
                self.stats_poller = RcloneStatsPoller(self.daemon.rc_credentials)
                self.stats_poller.stats_updated.connect(self.tray.update_stats)
                self.stats_poller.start()
            
            # Add to file manager sidebar with dynamic label
            remote_name = self.daemon.current_remote or "Drive"
            clean_remote = remote_name.rstrip(":")
            integrate_mount(self.daemon.mount_point, label=f"DucksDrive - {clean_remote}")
            
        elif state == "unmounting" or state == "stopped":
            # Stop polling
            if self.stats_poller:
                self.stats_poller.stop()
                self.stats_poller = None
            
            # Remove from file manager sidebar
            clean_integration(self.daemon.mount_point)

    def shutdown(self) -> None:
        """Graceful shutdown: unmount and exit."""
        logger.info("Shutting down application...")
        self.daemon.stop()  # This also calls clean_integration via signal handler
        self.app.quit()


def main() -> None:
    """Main entrypoint function."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 1. Enforce Single Instance via QLockFile
    lock_dir = os.path.expanduser("~/.config/ducksdrive")
    os.makedirs(lock_dir, exist_ok=True)
    lock_file_path = os.path.join(lock_dir, "app.lock")
    
    # Retain lock_file reference in main scope
    lock_file = QLockFile(lock_file_path)
    lock_file.setStaleLockTime(3000)
    if not lock_file.tryLock(100):
        QMessageBox.critical(
            None,
            "Already Running",
            "An instance of DucksDrive is already running in the system tray."
        )
        sys.exit(1)

    # 2. System Tray Availability Check
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "System Tray Not Found",
            "The system tray is not available. Please ensure a desktop environment is running."
        )
        sys.exit(1)

    # 3. Version Check
    version = get_rclone_version()
    if not version:
        QMessageBox.critical(None, "Rclone Not Found", 
                             "Rclone is not installed or could not be found in PATH.")
        sys.exit(1)

    if version < REQUIRED_RCLONE_VERSION:
        v_str = ".".join(map(str, version))
        req_str = ".".join(map(str, REQUIRED_RCLONE_VERSION))
        QMessageBox.warning(None, "Rclone Outdated", 
                            f"Detected Rclone v{v_str}.\nVersion v{req_str} or higher is recommended.")

    # 4. Initialize Controller
    controller = RcloneAppController(app)
    controller.run()

    # 5. Execute Loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
