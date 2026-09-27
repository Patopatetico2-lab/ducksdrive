"""
gui_tray.py - PySide6 System Tray Interface and Dialogs.

Provides the primary user interface including the System Tray icon,
context menus, status notifications, and dialogs for remote management.
"""

import logging
import os
import shutil
import subprocess
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QComboBox
)

from rclone_config import (
    SUPPORTED_REMOTE_TYPES,
    list_remote_names,
    RemoteCreationThread
)
from rclone_daemon import RcloneDaemon
from rclone_stats import StatsData
from status_popup import StatusPopup
from settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

APP_NAME = "DucksDrive"


def get_app_icon() -> QIcon:
    """
    Returns the application icon by loading the physical icon.svg file
    using an absolute path based on the script location.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(current_dir, "icon.svg")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        if not icon.isNull():
            return icon
    return QIcon.fromTheme("network-cloud", QIcon.fromTheme("folder-remote"))


class NewRemoteDialog(QDialog):
    """Dialog to gather details for creating a new rclone remote with duplicate validation."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure New Cloud")
        self.setMinimumWidth(350)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., my_google_drive")
        
        self.type_combo = QComboBox()
        for provider in SUPPORTED_REMOTE_TYPES:
            self.type_combo.addItem(provider["label"], provider["type"])
            
        form.addRow("Remote Name:", self.name_input)
        form.addRow("Cloud Provider:", self.type_combo)
        
        layout.addLayout(form)
        
        self.btn_create = QPushButton("Start Browser Auth")
        self.btn_create.clicked.connect(self.accept)
        layout.addWidget(self.btn_create)

    def accept(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Input Error", "Remote name cannot be empty.")
            return
        
        existing_remotes = list_remote_names()
        if name in existing_remotes:
            QMessageBox.warning(self, "Duplicate Error", f"A remote named '{name}' already exists.")
            return

        super().accept()
        
    def get_data(self) -> tuple[str, str]:
        return self.name_input.text().strip(), self.type_combo.currentData()


class RcloneTrayIcon(QSystemTrayIcon):
    """Main System Tray application logic."""
    
    request_exit = Signal()

    def __init__(self, daemon: RcloneDaemon, parent: Optional[QWidget] = None) -> None:
        super().__init__(get_app_icon(), parent)
        self.daemon = daemon
        self.setToolTip(f"{APP_NAME}: Idle")
        self._pending_remote_name: Optional[str] = None
        self.latest_stats: Optional[StatsData] = None
        
        # Initialize floating status popup panel
        self.status_popup = StatusPopup(daemon)
        
        self._menu = QMenu()
        self._setup_menu()
        self.setContextMenu(self._menu)
        
        self.daemon.state_changed.connect(self._on_state_changed)
        self.daemon.mount_error.connect(self._on_mount_error)
        
        self.activated.connect(self._on_activated)

    def _setup_menu(self) -> None:
        """Initializes the context menu actions."""
        self._menu.clear()
        
        self.action_status = QAction("Status: Disconnected", self)
        self.action_status.setEnabled(False)
        self._menu.addAction(self.action_status)
        
        self._menu.addSeparator()
        
        self.action_open = QAction("Open Drive Folder", self)
        self.action_open.triggered.connect(self._open_folder)
        self._menu.addAction(self.action_open)
        
        self.action_mount = QAction("Connect Drive...", self)
        self.action_mount.triggered.connect(self._show_mount_selector)
        self._menu.addAction(self.action_mount)
        
        self.action_unmount = QAction("Disconnect Drive", self)
        self.action_unmount.triggered.connect(self.daemon.stop)
        self.action_unmount.setVisible(False)
        self._menu.addAction(self.action_unmount)
        
        self._menu.addSeparator()
        
        self.action_new = QAction("Configure New Cloud...", self)
        self.action_new.triggered.connect(self._show_new_remote_dialog)
        self._menu.addAction(self.action_new)
        
        # Start with system autostart toggle
        autostart_file = os.path.expanduser("~/.config/autostart/ducksdrive.desktop")
        self.action_autostart = QAction("Start with system", self, checkable=True)
        self.action_autostart.setChecked(os.path.exists(autostart_file))
        self.action_autostart.triggered.connect(self._toggle_autostart)
        self._menu.addAction(self.action_autostart)

        # Settings dialog action
        self.action_settings = QAction("Settings...", self)
        self.action_settings.triggered.connect(self._show_settings_dialog)
        self._menu.addAction(self.action_settings)
        
        self._menu.addSeparator()
        
        self.action_exit = QAction("Quit", self)
        self.action_exit.triggered.connect(self._confirm_quit)
        self._menu.addAction(self.action_exit)

    def _show_settings_dialog(self) -> None:
        """Opens the settings configuration dialog."""
        dialog = SettingsDialog()
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.exec()

    def _toggle_autostart(self, checked: bool) -> None:
        """Enables or disables system autostart by managing ~/.config/autostart/ducksdrive.desktop."""
        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_file = os.path.join(autostart_dir, "ducksdrive.desktop")
        
        if checked:
            os.makedirs(autostart_dir, exist_ok=True)
            bin_path = shutil.which("ducksdrive") or os.path.expanduser("~/.local/bin/ducksdrive")
            if not os.path.exists(bin_path):
                bin_path = os.path.expanduser("~/.local/bin/ducksdrive")
            icon_path = os.path.expanduser("~/.local/share/ducksdrive/icon.svg")
            
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
                logger.info("Enabled system autostart.")
            except Exception as e:
                logger.error("Failed to enable autostart: %s", e)
        else:
            if os.path.exists(autostart_file):
                try:
                    os.remove(autostart_file)
                    logger.info("Disabled system autostart.")
                except Exception as e:
                    logger.error("Failed to disable autostart: %s", e)

    @Slot(str)
    def _on_state_changed(self, state: str) -> None:
        """Updates UI elements based on daemon state."""
        is_mounted = state == "mounted"
        self.action_mount.setVisible(not is_mounted)
        self.action_unmount.setVisible(is_mounted)
        self.action_open.setEnabled(is_mounted)
        
        status_map = {
            "stopped": "Disconnected",
            "starting": "Connecting...",
            "mounted": "Connected",
            "unmounting": "Disconnecting...",
            "error": "Error"
        }
        status_text = status_map.get(state, state.capitalize())
        self.action_status.setText(f"Status: {status_text}")
        self.setToolTip(f"{APP_NAME}: {status_text}")
        
        if is_mounted:
            self.showMessage(APP_NAME, "Drive mounted successfully.", QSystemTrayIcon.Information, 3000)

    @Slot(str)
    def _on_mount_error(self, error: str) -> None:
        QMessageBox.critical(None, "Mount Error", error)

    @Slot(object)
    def update_stats(self, stats: StatsData) -> None:
        """Updates the tray tooltip, menu status, and floating status popup with live telemetry."""
        self.latest_stats = stats
        if stats.is_online:
            self.action_status.setText(f"Speed: {stats.speed_str} | ETA: {stats.eta_str}")
            self.setToolTip(f"{APP_NAME}: {stats.status_text}")
        else:
            if self.daemon.state == "mounted":
                self.action_status.setText("Status: Connection Lost")

        # Forward telemetry to floating status popup
        self.status_popup.update_stats(stats)

    def _has_active_transfer(self) -> bool:
        """Checks if there is any active file transfer in progress."""
        if not self.latest_stats or not self.latest_stats.is_online:
            return False
        return self.latest_stats.transfers_count > 0 or len(self.latest_stats.active_transfers) > 0

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """
        Left-click (Trigger) toggles the floating status popup.
        Right-click (Context) is handled natively by setContextMenu().
        """
        if reason == QSystemTrayIcon.Trigger:
            if self.status_popup.isVisible():
                self.status_popup.close()
            else:
                self.status_popup.show_near_cursor()

    def _confirm_quit(self) -> None:
        """Confirms exit if active transfers are currently in progress."""
        if self._has_active_transfer():
            res = QMessageBox.warning(
                None,
                "Active Transfers",
                "File transfers are currently in progress.\nAre you sure you want to quit and interrupt them?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return
        self.request_exit.emit()

    def _open_folder(self) -> None:
        """Opens the mount point in the system file manager using xdg-open."""
        path = self.daemon.mount_point
        if os.path.exists(path):
            try:
                subprocess.Popen(["xdg-open", path])
            except Exception as e:
                logger.error("Failed to open folder with xdg-open: %s", e)
        else:
            QMessageBox.warning(None, "Folder Missing", f"The directory {path} does not exist.")

    def _show_mount_selector(self) -> None:
        """Shows a dialog to select which remote to mount."""
        remotes = list_remote_names()
        if not remotes:
            msg = "No cloud remotes configured.\nWould you like to configure one now?"
            res = QMessageBox.question(None, "No Remotes", msg, QMessageBox.Yes | QMessageBox.No)
            if res == QMessageBox.Yes:
                self._show_new_remote_dialog()
            return
            
        remote, ok = QInputDialog.getItem(
            None, "Connect Drive", "Select a cloud remote to mount:", remotes, 0, False
        )
        if ok and remote:
            self.daemon.start(remote)

    def _show_new_remote_dialog(self) -> None:
        """Handles the flow for creating a new remote via background thread."""
        dialog = NewRemoteDialog()
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.Accepted:
            name, rtype = dialog.get_data()
            if not name:
                QMessageBox.warning(None, "Input Error", "Remote name cannot be empty.")
                return

            self._pending_remote_name = name
            self._creation_thread = RemoteCreationThread(name, rtype)
            
            # Show a progress message since this can take time
            progress = QMessageBox(QMessageBox.Information, "Configuring", 
                                  f"Please complete OAuth in your browser for '{name}'...",
                                  QMessageBox.Cancel)
            progress.button(QMessageBox.Cancel).clicked.connect(self._creation_thread.cancel)
            
            self._creation_thread.finished_creation.connect(progress.close)
            self._creation_thread.finished_creation.connect(self._on_creation_finished)
            
            self._creation_thread.start()
            progress.exec()

    def _on_creation_finished(self, success: bool, message: str) -> None:
        """
        Handles remote creation completion. If successful, automatically initiates
        silent background FUSE mount after a 1-second delay to ensure config file flush.
        """
        if success:
            logger.info("Remote successfully configured. Starting silent mount for '%s'", self._pending_remote_name)
            if self._pending_remote_name:
                remote_name = self._pending_remote_name
                self._pending_remote_name = None
                # Add a 1-second QTimer delay to ensure rclone config file flush completes before mounting
                QTimer.singleShot(1000, lambda: self.daemon.start(remote_name))
        else:
            if "cancelled" not in message.lower():
                QMessageBox.critical(None, "Configuration Failed", message)
