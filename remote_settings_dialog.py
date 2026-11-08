"""
remote_settings_dialog.py - Per-Remote Configuration Dialog for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Allows defining drive-specific parameters (e.g. --drive-shared-with-me for Google Drive,
dedicated mount points, default read-only mode, and bandwidth/cache overrides).
"""

import logging
import os
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from rclone_config import get_remote_type_fast
from rclone_settings import (
    apply_app_theme,
    get_remote_config,
    load_config,
    save_remote_config,
)

logger = logging.getLogger(__name__)


class RemoteSettingsDialog(QDialog):
    """
    Configuration dialog for setting individual parameters per cloud remote.
    """

    def __init__(
        self,
        remote_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.remote_name = remote_name.rstrip(":")
        self.remote_type = get_remote_type_fast(self.remote_name) or ""
        self.remote_cfg = get_remote_config(self.remote_name)
        self.saved = False

        self.setWindowTitle(tr("Configurações da Nuvem: %s") % self.remote_name)
        self.setMinimumWidth(500)

        self._init_ui()
        self._load_values()
        apply_app_theme(str(load_config().get("theme") or "auto"))

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # Header with remote name and type
        header_layout = QHBoxLayout()
        title_label = QLabel(f"<b>{self.remote_name}</b>")
        title_label.setStyleSheet("font-size: 13pt; font-weight: bold; color: #2980b9;")
        header_layout.addWidget(title_label)

        if self.remote_type:
            type_label = QLabel(f"[{self.remote_type.upper()}]")
            type_label.setStyleSheet("font-size: 9pt; color: #7f8c8d; font-weight: bold;")
            header_layout.addWidget(type_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Group 1: Mount Path & Access Mode
        mount_group = QGroupBox(tr("Ponto de Montagem e Acesso"))
        mount_layout = QFormLayout(mount_group)
        mount_layout.setSpacing(10)

        mp_row = QHBoxLayout()
        self.mount_path_edit = QLineEdit()
        self.mount_path_edit.setPlaceholderText(
            os.path.expanduser(f"~/CloudDrives/{self.remote_name}") + " (" + tr("Padrão") + ")"
        )
        self.btn_browse = QPushButton(tr("Procurar..."))
        self.btn_browse.clicked.connect(self._on_browse_mount_point)
        mp_row.addWidget(self.mount_path_edit)
        mp_row.addWidget(self.btn_browse)
        mount_layout.addRow(tr("Ponto de Montagem Personalizado:"), mp_row)

        self.ro_checkbox = QCheckBox(tr("Montar sempre como Somente Leitura (--read-only)"))
        mount_layout.addRow("", self.ro_checkbox)
        main_layout.addWidget(mount_group)

        # Group 2: Custom Flags
        flags_group = QGroupBox(tr("Flags e Parâmetros Avançados do Rclone"))
        flags_layout = QVBoxLayout(flags_group)
        flags_layout.setSpacing(8)

        self.flags_edit = QLineEdit()
        self.flags_edit.setPlaceholderText(tr("ex: --drive-shared-with-me --fast-list"))
        flags_layout.addWidget(self.flags_edit)

        # Special helper for Google Drive
        if self.remote_type == "drive":
            self.shared_with_me_check = QCheckBox(
                tr("Incluir pastas compartilhadas comigo (--drive-shared-with-me)")
            )
            self.shared_with_me_check.toggled.connect(self._toggle_shared_with_me)
            flags_layout.addWidget(self.shared_with_me_check)
        else:
            self.shared_with_me_check = None

        hint_label = QLabel(
            tr("Dica: As flags informadas serão adicionadas ao comando rclone mount específico deste drive.")
        )
        hint_label.setStyleSheet("font-size: 8pt; color: #7f8c8d; font-style: italic;")
        hint_label.setWordWrap(True)
        flags_layout.addWidget(hint_label)

        main_layout.addWidget(flags_group)

        # Group 3: Performance Overrides (Optional)
        perf_group = QGroupBox(tr("Substituições de Desempenho (Opcional)"))
        perf_layout = QFormLayout(perf_group)
        perf_layout.setSpacing(10)

        self.bwlimit_combo = QComboBox()
        self.bwlimit_combo.addItem(tr("Herdar configuração global"), "")
        self.bwlimit_combo.addItem(tr("Ilimitado (Sem teto de banda)"), "off")
        self.bwlimit_combo.addItem("2 MB/s", "2M")
        self.bwlimit_combo.addItem("5 MB/s", "5M")
        self.bwlimit_combo.addItem("10 MB/s", "10M")
        self.bwlimit_combo.addItem("25 MB/s", "25M")
        self.bwlimit_combo.addItem("50 MB/s", "50M")
        perf_layout.addRow(tr("Limite de Banda:"), self.bwlimit_combo)

        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(0, 500)
        self.cache_spin.setSpecialValueText(tr("Herdar configuração global"))
        self.cache_spin.setSuffix(" GB")
        self.cache_spin.setValue(0)
        perf_layout.addRow(tr("Tamanho Máximo do Cache:"), self.cache_spin)

        main_layout.addWidget(perf_group)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton(tr("Guardar"))
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 6px 16px; border-radius: 4px; font-weight: bold;"
        )
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _load_values(self) -> None:
        self.mount_path_edit.setText(str(self.remote_cfg.get("custom_mount_point") or ""))
        self.ro_checkbox.setChecked(bool(self.remote_cfg.get("read_only", False)))

        flags = str(self.remote_cfg.get("custom_flags") or "")
        self.flags_edit.setText(flags)
        if self.shared_with_me_check is not None:
            self.shared_with_me_check.setChecked("--drive-shared-with-me" in flags)

        bw = str(self.remote_cfg.get("bwlimit") or "")
        idx = self.bwlimit_combo.findData(bw)
        if idx >= 0:
            self.bwlimit_combo.setCurrentIndex(idx)

        cache_str = str(self.remote_cfg.get("vfs_cache_max_size") or "").strip().upper()
        if cache_str.endswith("G") and cache_str[:-1].isdigit():
            self.cache_spin.setValue(int(cache_str[:-1]))
        elif cache_str.isdigit():
            self.cache_spin.setValue(int(cache_str))
        else:
            self.cache_spin.setValue(0)

    def _toggle_shared_with_me(self, checked: bool) -> None:
        current_flags = self.flags_edit.text()
        flag = "--drive-shared-with-me"
        parts = current_flags.split()
        if checked:
            if flag not in parts:
                parts.append(flag)
        else:
            parts = [p for p in parts if p != flag]
        self.flags_edit.setText(" ".join(parts))

    def _on_browse_mount_point(self) -> None:
        initial = self.mount_path_edit.text().strip() or os.path.expanduser(f"~/CloudDrives/{self.remote_name}")
        path = QFileDialog.getExistingDirectory(self, tr("Selecionar Ponto de Montagem"), initial)
        if path:
            self.mount_path_edit.setText(path)

    def _on_save(self) -> None:
        custom_mp = self.mount_path_edit.text().strip()
        if custom_mp:
            custom_mp = os.path.abspath(os.path.expanduser(custom_mp))

        read_only = self.ro_checkbox.isChecked()
        flags = self.flags_edit.text().strip()
        bw = str(self.bwlimit_combo.currentData() or "")
        cache_gb = self.cache_spin.value()
        cache_str = f"{cache_gb}G" if cache_gb > 0 else ""

        updated_cfg = {
            "custom_mount_point": custom_mp,
            "read_only": read_only,
            "custom_flags": flags,
            "bwlimit": bw,
            "vfs_cache_max_size": cache_str,
        }

        if save_remote_config(self.remote_name, updated_cfg):
            self.saved = True
            self.remote_cfg = updated_cfg
            self.accept()
        else:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao gravar o ficheiro de configuração."))
