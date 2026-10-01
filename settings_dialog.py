"""
settings_dialog.py - Configuration Dialog for DucksDrive.

Provides a user-friendly settings dialog with safe GUI controls (QSpinBox, QComboBox)
organized into semantic QGroupBox sections for bandwidth, cache, and system preferences.
"""

import logging
import re
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget
)

from rclone_settings import load_config, save_config

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Settings dialog allowing users to adjust Rclone performance limits
    and system integration preferences using safe controls instead of free text inputs.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DucksDrive • Configurações")
        self.setMinimumWidth(480)
        
        self.config = load_config()
        self._init_ui()
        self._load_values()

    def _init_ui(self) -> None:
        """Initializes grouped form layouts with QGroupBox controls."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        title_label = QLabel("<b>Preferências de Desempenho e Sistema</b>")
        title_label.setStyleSheet("font-size: 13pt; color: #2c3e50;")
        main_layout.addWidget(title_label)

        # 1. Grupo Rede & Desempenho
        net_group = QGroupBox("Rede e Transferência")
        net_layout = QFormLayout(net_group)
        net_layout.setSpacing(10)

        self.bwlimit_combo = QComboBox()
        self.bwlimit_combo.addItem("Ilimitado (Sem teto de banda)", "off")
        self.bwlimit_combo.addItem("2 MB/s (Modesto)", "2M")
        self.bwlimit_combo.addItem("5 MB/s (Equilibrado)", "5M")
        self.bwlimit_combo.addItem("10 MB/s (Rápido)", "10M")
        self.bwlimit_combo.addItem("25 MB/s (Muito Rápido)", "25M")
        self.bwlimit_combo.addItem("50 MB/s (Máximo)", "50M")
        net_layout.addRow("Limite de Banda:", self.bwlimit_combo)

        self.transfers_spin = QSpinBox()
        self.transfers_spin.setRange(1, 16)
        self.transfers_spin.setValue(4)
        net_layout.addRow("Transferências Paralelas:", self.transfers_spin)

        main_layout.addWidget(net_group)

        # 2. Grupo Armazenamento & Cache
        cache_group = QGroupBox("Armazenamento e Cache SSD")
        cache_layout = QFormLayout(cache_group)
        cache_layout.setSpacing(10)

        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(1, 500)
        self.cache_spin.setSuffix(" GB")
        self.cache_spin.setValue(2)
        cache_layout.addRow("Tamanho Máximo do Cache VFS:", self.cache_spin)

        main_layout.addWidget(cache_group)

        # 3. Grupo Geral / Sistema
        sys_group = QGroupBox("Integração com o Sistema")
        sys_layout = QVBoxLayout(sys_group)
        
        self.autostart_checkbox = QCheckBox("Iniciar DucksDrive automaticamente com o sistema operativo")
        sys_layout.addWidget(self.autostart_checkbox)

        main_layout.addWidget(sys_group)

        # Buttons layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_cancel.setStyleSheet("padding: 6px 14px;")
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Guardar Configurações")
        self.btn_save.clicked.connect(self._save_and_accept)
        self.btn_save.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 6px 14px; border-radius: 4px; font-weight: bold;"
        )
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _load_values(self) -> None:
        """Populates form controls with values loaded from config with proper unit parsing."""
        bwlimit_val = str(self.config.get("bwlimit", "off"))
        index = self.bwlimit_combo.findData(bwlimit_val)
        if index >= 0:
            self.bwlimit_combo.setCurrentIndex(index)
        else:
            # Add custom bandwidth limit dynamically if not in presets
            self.bwlimit_combo.addItem(f"Personalizado ({bwlimit_val})", bwlimit_val)
            self.bwlimit_combo.setCurrentIndex(self.bwlimit_combo.count() - 1)

        transfers_val = int(self.config.get("transfers", 4))
        self.transfers_spin.setValue(transfers_val)

        cache_str = str(self.config.get("vfs_cache_max_size", "2G")).strip().upper()
        cache_gb = 2
        try:
            match = re.match(r"^(\d+)([KMGT]?)$", cache_str)
            if match:
                val, unit = match.groups()
                num = int(val)
                if unit == "K":
                    cache_gb = max(1, num // (1024 * 1024))
                elif unit == "M":
                    cache_gb = max(1, num // 1024)
                elif unit == "T":
                    cache_gb = num * 1024
                else:  # G or empty
                    cache_gb = num
            else:
                digits = ''.join(filter(str.isdigit, cache_str))
                if digits:
                    cache_gb = int(digits)
        except Exception:
            pass
        self.cache_spin.setValue(max(1, min(cache_gb, 500)))

        self.autostart_checkbox.setChecked(bool(self.config.get("autostart", True)))

    def _save_and_accept(self) -> None:
        """Validates controls, warns on high SSD cache size, saves to config, and closes."""
        bwlimit = self.bwlimit_combo.currentData() or "off"
        transfers = self.transfers_spin.value()
        cache_gb = self.cache_spin.value()
        cache_size = f"{cache_gb}G"

        # Validate high cache size warning if >= 50 GB
        if cache_gb >= 50:
            res = QMessageBox.warning(
                self,
                "Aviso de Cache Elevado",
                f"Atenção: Um tamanho de cache VFS de {cache_gb} GB é muito elevado e corre o risco de esgotar o espaço do seu SSD!\n\nDeseja prosseguir?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return

        autostart = self.autostart_checkbox.isChecked()

        # Update config dict
        self.config["bwlimit"] = bwlimit
        self.config["vfs_cache_max_size"] = cache_size
        self.config["transfers"] = transfers
        self.config["autostart"] = autostart

        # Save to disk
        if save_config(self.config):
            QMessageBox.information(self, "Configuração Guardada", "Preferências atualizadas com sucesso.\nAs alterações serão aplicadas na próxima conexão.")
            self.accept()
        else:
            QMessageBox.critical(self, "Erro", "Falha ao gravar o ficheiro de configuração.")
