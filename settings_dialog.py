"""
settings_dialog.py - Configuration Dialog for DucksDrive.

Provides a user-friendly settings dialog with friendly explanations
for bandwidth limit, cache size, parallel transfers, and autostart.
"""

import logging
import os
import shutil
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget
)

from rclone_settings import load_config, save_config

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Settings dialog allowing users to adjust Rclone performance limits
    and system integration preferences with clear, layman-friendly explanations.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DucksDrive Settings")
        self.setMinimumWidth(450)
        
        self.config = load_config()
        self._init_ui()
        self._load_values()

    def _init_ui(self) -> None:
        """Initializes form layout, inputs, and explanatory text labels."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        title_label = QLabel("<b>Performance & System Preferences</b>")
        title_label.setStyleSheet("font-size: 13pt; color: #2c3e50;")
        main_layout.addWidget(title_label)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        # 1. Bandwidth Limit (--bwlimit)
        self.bwlimit_input = QLineEdit()
        self.bwlimit_input.setPlaceholderText("e.g., 10M, 5M, off")
        bwlimit_label_layout = QVBoxLayout()
        bwlimit_label_layout.setSpacing(2)
        bwlimit_label_layout.addWidget(self.bwlimit_input)
        bwlimit_desc = QLabel(
            "Internet speed ceiling (e.g. '10M' for 10 MB/s, or 'off' for unlimited).\n"
            "• Too low: Intentionally slow.\n"
            "• Too high: May saturate home bandwidth and lag online games/streaming."
        )
        bwlimit_desc.setStyleSheet("color: #7f8c8d; font-size: 9pt;")
        bwlimit_label_layout.addWidget(bwlimit_desc)
        form_layout.addRow("Bandwidth Limit:", bwlimit_label_layout)

        # 2. Max VFS Cache Size (--vfs-cache-max-size)
        self.cache_size_input = QLineEdit()
        self.cache_size_input.setPlaceholderText("e.g., 2G, 500M")
        cache_label_layout = QVBoxLayout()
        cache_label_layout.setSpacing(2)
        cache_label_layout.addWidget(self.cache_size_input)
        cache_desc = QLabel(
            "Maximum SSD storage reserved for temporary file copies (e.g. '2G').\n"
            "• Too low: Clears cache quickly, forcing constant re-downloads.\n"
            "• Too high: Accumulates temporary files until filling up your SSD."
        )
        cache_desc.setStyleSheet("color: #7f8c8d; font-size: 9pt;")
        cache_label_layout.addWidget(cache_desc)
        form_layout.addRow("Max VFS Cache Size:", cache_label_layout)

        # 3. Parallel Transfers (--transfers)
        self.transfers_input = QLineEdit()
        self.transfers_input.setPlaceholderText("e.g., 4")
        transfers_label_layout = QVBoxLayout()
        transfers_label_layout.setSpacing(2)
        transfers_label_layout.addWidget(self.transfers_input)
        transfers_desc = QLabel(
            "Number of files uploaded or downloaded simultaneously (e.g. 4).\n"
            "• Too low: Slow for bulk file syncs.\n"
            "• Too high: Can choke your home router and trigger connection errors."
        )
        transfers_desc.setStyleSheet("color: #7f8c8d; font-size: 9pt;")
        transfers_label_layout.addWidget(transfers_desc)
        form_layout.addRow("Parallel Transfers:", transfers_label_layout)

        # 4. Start with system (Autostart)
        self.autostart_checkbox = QCheckBox("Start DucksDrive automatically when system boots")
        form_layout.addRow("System Startup:", self.autostart_checkbox)

        main_layout.addLayout(form_layout)

        # Buttons layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_cancel.setStyleSheet("padding: 6px 14px;")
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save Settings")
        self.btn_save.clicked.connect(self._save_and_accept)
        self.btn_save.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 6px 14px; border-radius: 4px; font-weight: bold;"
        )
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _load_values(self) -> None:
        """Populates form fields with values loaded from config."""
        self.bwlimit_input.setText(str(self.config.get("bwlimit", "off")))
        self.cache_size_input.setText(str(self.config.get("vfs_cache_max_size", "2G")))
        self.transfers_input.setText(str(self.config.get("transfers", 4)))
        self.autostart_checkbox.setChecked(bool(self.config.get("autostart", True)))

    def _save_and_accept(self) -> None:
        """Validates inputs, saves to config file, updates autostart desktop file, and closes."""
        bwlimit = self.bwlimit_input.text().strip() or "off"
        cache_size = self.cache_size_input.text().strip() or "2G"
        
        try:
            transfers = int(self.transfers_input.text().strip() or "4")
            if transfers < 1:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Parallel transfers must be a positive integer.")
            return

        autostart = self.autostart_checkbox.isChecked()

        # Update config dict
        self.config["bwlimit"] = bwlimit
        self.config["vfs_cache_max_size"] = cache_size
        self.config["transfers"] = transfers
        self.config["autostart"] = autostart

        # Save to disk
        if save_config(self.config):
            # Synchronize system autostart state
            self._sync_autostart(autostart)
            QMessageBox.information(self, "Settings Saved", "Preferences updated successfully.\nChanges will apply on next drive connection.")
            self.accept()
        else:
            QMessageBox.critical(self, "Error", "Failed to save configuration file.")

    def _sync_autostart(self, enabled: bool) -> None:
        """Creates or removes the autostart .desktop file."""
        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_file = os.path.join(autostart_dir, "ducksdrive.desktop")
        
        if enabled:
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
