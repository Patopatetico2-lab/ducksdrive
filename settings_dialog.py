"""
settings_dialog.py - Configuration Dialog for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Provides a user-friendly settings dialog with safe GUI controls (QSpinBox, QComboBox)
organized into semantic QGroupBox sections for bandwidth, cache, and system preferences.
"""

import logging
import os
import re
import shutil
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget
)

from rclone_config import TaskRunner
from rclone_settings import (
    CONFIG_DIR,
    apply_app_theme,
    clear_vfs_cache_files,
    get_vfs_cache_usage_bytes,
    is_dark_theme_active,
    load_config,
    save_config,
)
from rclone_stats import fetch_rclone_stats, fetch_vfs_queue, format_bytes
from i18n import set_language, tr

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Settings dialog allowing users to adjust Rclone performance limits
    and system integration preferences using safe controls instead of free text inputs.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        daemon: Optional[Any] = None,
        runner: Optional[TaskRunner] = None,
        daemons: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.daemon = daemon
        self.daemons: dict[str, Any] = daemons if daemons is not None else {}
        self.runner = runner or getattr(daemon, "_integration_runner", None)
        self._cache_runner = TaskRunner(self)
        self._clearing_cache = False
        self._log_viewer_dialog: Optional[Any] = None
        self._update_worker: Optional[Any] = None
        self.language_changed = False
        self.bwlimit_changed = False
        self.theme_changed = False
        self.setWindowTitle(tr("Configurações do DucksDrive"))
        self.setMinimumWidth(655)

        self.config = load_config()
        self._init_ui()
        self._load_values()
        self.apply_theme(str(self.theme_combo.currentData() or self.config.get("theme") or "auto"))
        self._refresh_cache_usage()

    def _get_known_daemons(self) -> list[Any]:
        """Returns a deduplicated list of all known RcloneDaemon instances."""
        items: list[Any] = []
        seen_ids: set[int] = set()
        for d in self.daemons.values():
            if d is not None and id(d) not in seen_ids:
                seen_ids.add(id(d))
                items.append(d)
        if self.daemon is not None and id(self.daemon) not in seen_ids:
            items.append(self.daemon)
        return items

    def _get_active_daemons(self) -> list[Any]:
        """Returns all currently running RcloneDaemon instances."""
        return [d for d in self._get_known_daemons() if d.is_running()]

    @staticmethod
    def _create_aligned_form(parent: QWidget) -> QFormLayout:
        """Creates a QFormLayout with expanding input fields for consistent alignment."""
        form = QFormLayout(parent)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return form

    @staticmethod
    def _aligned_label(text: str) -> QLabel:
        """Creates a fixed-minimum-width label so columns line up across every QGroupBox."""
        lbl = QLabel(text)
        lbl.setMinimumWidth(205)
        return lbl

    def _init_ui(self) -> None:
        """Initializes cleanly grouped, vertically aligned form sections and action bars."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)

        # Header Row: Title + Version pill
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 2)
        self.title_label = QLabel(tr("Preferências de Desempenho e Sistema"))
        self.title_label.setStyleSheet("font-size: 13pt; font-weight: bold;")
        header_row.addWidget(self.title_label)
        header_row.addStretch()

        from updater import APP_VERSION
        self.version_badge = QLabel(f"v{APP_VERSION}")
        header_row.addWidget(self.version_badge)
        main_layout.addLayout(header_row)

        # 1. Grupo Geral e Aparência
        general_group = QGroupBox(tr("Geral e Aparência"))
        general_outer = QVBoxLayout(general_group)
        general_outer.setContentsMargins(12, 12, 12, 12)
        general_outer.setSpacing(9)

        lang_layout = QFormLayout()
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.setHorizontalSpacing(12)
        lang_layout.setVerticalSpacing(9)
        lang_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.lang_combo = QComboBox()
        self.lang_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.lang_combo.addItem(tr("Português (Brasil)"), "pt_BR")
        self.lang_combo.addItem(tr("Inglês (EUA)"), "en_US")
        lang_layout.addRow(self._aligned_label(tr("Idioma / Language:")), self.lang_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.theme_combo.addItem(tr("Automático (Sistema)"), "auto")
        self.theme_combo.addItem(tr("Claro (Light)"), "light")
        self.theme_combo.addItem(tr("Escuro (Dark)"), "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        lang_layout.addRow(self._aligned_label(tr("Tema do Painel:")), self.theme_combo)
        general_outer.addLayout(lang_layout)

        self.autostart_checkbox = QCheckBox(tr("Iniciar DucksDrive automaticamente com o sistema operativo"))
        general_outer.addWidget(self.autostart_checkbox)
        main_layout.addWidget(general_group)

        # 2. Grupo Rede & Desempenho
        net_group = QGroupBox(tr("Rede e Transferência"))
        net_layout = self._create_aligned_form(net_group)

        self.profile_combo = QComboBox()
        self.profile_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.profile_combo.addItem(tr("Equilibrado (Dolphin Rápido + Multi-Stream)"), "balanced")
        self.profile_combo.addItem(tr("Downloads Grandes e Streaming (Buffer 32M / Read-Ahead 64M)"), "streaming")
        self.profile_combo.addItem(tr("Upload Seguro (Evita Saturar a Rede / Bufferbloat)"), "safe_upload")
        net_layout.addRow(self._aligned_label(tr("Perfil de Desempenho:")), self.profile_combo)

        self.bwlimit_combo = QComboBox()
        self.bwlimit_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.bwlimit_combo.addItem(tr("Ilimitado (Sem teto de banda)"), "off")
        self.bwlimit_combo.addItem(tr("Upload 2 MB/s • Download Ilimitado"), "2M:off")
        self.bwlimit_combo.addItem(tr("Upload 4 MB/s • Download Ilimitado (Anti-Lag)"), "4M:off")
        self.bwlimit_combo.addItem(tr("Upload 10 MB/s • Download Ilimitado"), "10M:off")
        self.bwlimit_combo.addItem(tr("Upload 25 MB/s • Download Ilimitado"), "25M:off")
        self.bwlimit_combo.addItem(tr("1 MB/s (Econômico)"), "1M")
        self.bwlimit_combo.addItem(tr("2 MB/s (Modesto)"), "2M")
        self.bwlimit_combo.addItem(tr("5 MB/s (Equilibrado)"), "5M")
        self.bwlimit_combo.addItem(tr("10 MB/s (Rápido)"), "10M")
        self.bwlimit_combo.addItem(tr("25 MB/s (Muito Rápido)"), "25M")
        self.bwlimit_combo.addItem(tr("50 MB/s (Máximo)"), "50M")
        self.bwlimit_combo.addItem(tr("100 MB/s (Ultra Rápido)"), "100M")
        net_layout.addRow(self._aligned_label(tr("Limite de Banda:")), self.bwlimit_combo)

        self.transfers_spin = QSpinBox()
        self.transfers_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.transfers_spin.setRange(1, 16)
        self.transfers_spin.setValue(4)
        self.transfers_spin.setToolTip(
            tr("Recomendado: 4 a 6 para Google Drive/OneDrive (valores muito altos podem causar rate-limit da API em pastas com milhares de arquivos).")
        )
        net_layout.addRow(self._aligned_label(tr("Transferências Paralelas:")), self.transfers_spin)
        main_layout.addWidget(net_group)

        # 3. Grupo Armazenamento & Cache
        cache_group = QGroupBox(tr("Armazenamento e Cache SSD"))
        cache_layout = self._create_aligned_form(cache_group)

        self.vfs_mode_combo = QComboBox()
        self.vfs_mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.vfs_mode_combo.addItem(tr("Full (Recomendado)"), "full")
        self.vfs_mode_combo.addItem(tr("Writes"), "writes")
        self.vfs_mode_combo.addItem(tr("Minimal"), "minimal")
        self.vfs_mode_combo.addItem(tr("Off"), "off")
        self.vfs_mode_combo.currentIndexChanged.connect(self._on_vfs_mode_changed)
        cache_layout.addRow(self._aligned_label(tr("Modo do Cache VFS:")), self.vfs_mode_combo)

        self.cache_spin = QSpinBox()
        self.cache_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cache_spin.setRange(1, 500)
        self.cache_spin.setSuffix(" GB")
        self.cache_spin.setValue(2)
        cache_layout.addRow(self._aligned_label(tr("Tamanho Máximo do Cache VFS:")), self.cache_spin)

        self.cache_age_combo = QComboBox()
        self.cache_age_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cache_age_combo.addItem(tr("1 Hora (Economizar SSD)"), "1h")
        self.cache_age_combo.addItem(tr("6 Horas"), "6h")
        self.cache_age_combo.addItem(tr("24 Horas (Recomendado)"), "24h")
        self.cache_age_combo.addItem(tr("3 Dias"), "72h")
        self.cache_age_combo.addItem(tr("7 Dias"), "168h")
        cache_layout.addRow(self._aligned_label(tr("Retenção do Cache no SSD:")), self.cache_age_combo)

        cache_usage_row = QHBoxLayout()
        cache_usage_row.setSpacing(8)
        self.cache_usage_label = QLabel(tr("Calculando espaço..."))
        self.cache_usage_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        self.btn_clear_cache = QPushButton(tr("Limpar Cache Agora"))
        self.btn_clear_cache.setFixedHeight(28)
        self.btn_clear_cache.setMinimumWidth(152)
        self.btn_clear_cache.setCursor(Qt.PointingHandCursor)
        self.btn_clear_cache.clicked.connect(self._on_clear_cache)
        cache_usage_row.addWidget(self.cache_usage_label)
        cache_usage_row.addStretch()
        cache_usage_row.addWidget(self.btn_clear_cache)
        cache_layout.addRow(self._aligned_label(tr("Uso Atual do Cache no SSD:")), cache_usage_row)
        main_layout.addWidget(cache_group)

        # 4. Grupo Segurança e Manutenção
        maint_group = QGroupBox(tr("Segurança e Manutenção"))
        maint_layout = QVBoxLayout(maint_group)
        maint_layout.setContentsMargins(12, 12, 12, 12)
        maint_layout.setSpacing(10)

        keyring_row = QHBoxLayout()
        keyring_row.setSpacing(8)
        self.keyring_checkbox = QCheckBox(tr("Proteger rclone.conf com o Chaveiro do Sistema (Secret Service / KWallet)"))
        self.btn_manage_keyring = QPushButton(tr("Configurar Senha..."))
        self.btn_manage_keyring.setFixedHeight(28)
        self.btn_manage_keyring.setMinimumWidth(152)
        self.btn_manage_keyring.setCursor(Qt.PointingHandCursor)
        self.btn_manage_keyring.clicked.connect(self._on_manage_keyring)
        keyring_row.addWidget(self.keyring_checkbox, 1)
        keyring_row.addWidget(self.btn_manage_keyring)
        maint_layout.addLayout(keyring_row)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)

        self.btn_check_update = QPushButton(tr("Verificar Atualizações"))
        self.btn_check_update.setFixedHeight(30)
        self.btn_check_update.setCursor(Qt.PointingHandCursor)
        self.btn_check_update.clicked.connect(self._on_check_updates)
        tools_row.addWidget(self.btn_check_update, 1)

        self.btn_view_logs = QPushButton(tr("Ver Logs de Diagnóstico"))
        self.btn_view_logs.setFixedHeight(30)
        self.btn_view_logs.setCursor(Qt.PointingHandCursor)
        self.btn_view_logs.clicked.connect(self._on_view_logs)
        tools_row.addWidget(self.btn_view_logs, 1)

        self.btn_uninstall = QPushButton(tr("Desinstalar"))
        self.btn_uninstall.setFixedHeight(30)
        self.btn_uninstall.setCursor(Qt.PointingHandCursor)
        self.btn_uninstall.clicked.connect(self._on_uninstall)
        tools_row.addWidget(self.btn_uninstall, 1)

        maint_layout.addLayout(tools_row)
        main_layout.addWidget(maint_group)

        # 5. Footer Dialog Actions (Cancel / Save only)
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.setFixedHeight(32)
        self.btn_cancel.setMinimumWidth(100)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton(tr("Guardar Configurações"))
        self.btn_save.setFixedHeight(32)
        self.btn_save.setMinimumWidth(160)
        self.btn_save.setDefault(True)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_and_accept)
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _load_values(self) -> None:
        """Populates form controls with values loaded from config with proper unit parsing."""
        prof_val = str(self.config.get("performance_profile", "balanced"))
        p_idx = self.profile_combo.findData(prof_val)
        if p_idx >= 0:
            self.profile_combo.setCurrentIndex(p_idx)

        bwlimit_val = str(self.config.get("bwlimit", "off"))
        index = self.bwlimit_combo.findData(bwlimit_val)
        if index >= 0:
            self.bwlimit_combo.setCurrentIndex(index)
        else:
            # Add custom bandwidth limit dynamically if not in presets
            self.bwlimit_combo.addItem(tr("Personalizado (%s)") % bwlimit_val, bwlimit_val)
            self.bwlimit_combo.setCurrentIndex(self.bwlimit_combo.count() - 1)

        try:
            transfers_val = int(self.config.get("transfers", 4))
        except (ValueError, TypeError):
            transfers_val = 4
        self.transfers_spin.setValue(transfers_val)

        cache_str = str(self.config.get("vfs_cache_max_size", "2G")).strip().upper()
        cache_gb = 2
        try:
            match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT])?I?B?$", cache_str)
            if match:
                val_str, unit = match.groups()
                num = float(val_str)
                if unit == "K":
                    cache_gb = max(1, int(round(num / (1024.0 * 1024.0))))
                elif unit == "M":
                    cache_gb = max(1, int(round(num / 1024.0)))
                elif unit == "T":
                    cache_gb = int(round(num * 1024.0))
                else:  # G or empty
                    cache_gb = int(round(num))
        except Exception:
            pass
        self.cache_spin.setValue(max(1, min(cache_gb, 500)))

        cache_age = str(self.config.get("vfs_cache_max_age", "24h"))
        age_idx = self.cache_age_combo.findData(cache_age)
        if age_idx >= 0:
            self.cache_age_combo.setCurrentIndex(age_idx)
        else:
            self.cache_age_combo.addItem(tr("Personalizado (%s)") % cache_age, cache_age)
            self.cache_age_combo.setCurrentIndex(self.cache_age_combo.count() - 1)

        vfs_mode = str(self.config.get("vfs_cache_mode", "full"))
        index = self.vfs_mode_combo.findData(vfs_mode)
        if index >= 0:
            self.vfs_mode_combo.setCurrentIndex(index)
        self._on_vfs_mode_changed()

        self.autostart_checkbox.setChecked(bool(self.config.get("autostart", True)))

        lang = str(self.config.get("language", "pt_BR"))
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

        theme = str(self.config.get("theme", "auto"))
        t_idx = self.theme_combo.findData(theme)
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)

        from keyring_manager import is_keyring_available
        keyring_avail = is_keyring_available()
        self.keyring_checkbox.setChecked(bool(self.config.get("use_system_keyring", False)) and keyring_avail)
        if not keyring_avail:
            self.keyring_checkbox.setEnabled(False)
            self.keyring_checkbox.setToolTip(tr("O utilitário 'secret-tool' não foi encontrado no sistema."))
            self.btn_manage_keyring.setEnabled(False)

    def _on_vfs_mode_changed(self, _index: int = 0) -> None:
        """Disables max VFS cache size spinbox and retention combo when cache mode is 'off' or 'minimal'."""
        mode = self.vfs_mode_combo.currentData()
        enabled = mode not in ("off", "minimal")
        self.cache_spin.setEnabled(enabled)
        self.cache_age_combo.setEnabled(enabled)

    def _on_theme_combo_changed(self, _index: int = 0) -> None:
        """Applies the selected theme immediately when the user changes the theme combo."""
        selected_theme = str(self.theme_combo.currentData() or "auto")
        self.apply_theme(selected_theme)

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies native QPalette and cohesive control styling for the specified theme."""
        active_pref = str(theme_pref or self.theme_combo.currentData() or self.config.get("theme") or "auto")
        is_dark = is_dark_theme_active(active_pref)
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
            for child in self.findChildren(QWidget):
                child.setPalette(palette)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        badge_bg = "#2a2f36" if is_dark else "#e9edf2"
        badge_fg = "#aeb6bf" if is_dark else "#566573"
        badge_bd = "#3b424c" if is_dark else "#cfd6de"
        self.version_badge.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_fg}; border: 1px solid {badge_bd}; "
            f"border-radius: 9px; padding: 2px 10px; font-size: 8.5pt; font-weight: bold;"
        )

        sec_bg = "#2d3239" if is_dark else "#f1f3f6"
        sec_fg = "#eff0f1" if is_dark else "#2c3e50"
        sec_bd = "#424852" if is_dark else "#cfd6de"
        sec_hv = "#39404a" if is_dark else "#e3e7ed"
        dis_bg = "#24282e" if is_dark else "#f4f6f8"
        dis_fg = "#6e7681" if is_dark else "#9aa5b1"
        sec_qss = f"""
            QPushButton {{
                background-color: {sec_bg};
                color: {sec_fg};
                border: 1px solid {sec_bd};
                border-radius: 5px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: {sec_hv};
            }}
            QPushButton:disabled {{
                background-color: {dis_bg};
                color: {dis_fg};
                border-color: transparent;
            }}
        """
        for btn in (self.btn_clear_cache, self.btn_manage_keyring, self.btn_check_update, self.btn_view_logs, self.btn_cancel):
            btn.setStyleSheet(sec_qss)

        danger_bg = "#3b2024" if is_dark else "#fdecea"
        danger_fg = "#ff7675" if is_dark else "#c0392b"
        danger_bd = "#6e2f35" if is_dark else "#f5b7b1"
        danger_hv = "#c0392b"
        self.btn_uninstall.setStyleSheet(f"""
            QPushButton {{
                background-color: {danger_bg};
                color: {danger_fg};
                border: 1px solid {danger_bd};
                border-radius: 5px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: {danger_hv};
                color: white;
                border-color: {danger_hv};
            }}
        """)

        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #2980b9;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px 16px;
                font-weight: bold;
                font-size: 9.5pt;
            }
            QPushButton:hover {
                background-color: #3498db;
            }
        """)

        usage_col = "#5dade2" if is_dark else "#2980b9"
        self.cache_usage_label.setStyleSheet(f"font-weight: bold; color: {usage_col};")

    def reject(self) -> None:
        """Restores the previously saved theme if the user cancels the dialog."""
        apply_app_theme(str(self.config.get("theme") or "auto"))
        super().reject()

    def _on_check_updates(self) -> None:
        """Runs update check in background worker without freezing dialog."""
        from updater import APP_VERSION, UpdateCheckWorker
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText(tr("Verificando..."))

        worker = UpdateCheckWorker(force=True, parent=self)
        self._update_worker = worker

        def _on_finished(has_update: bool, latest_ver: str, url: str, notes: str) -> None:
            if not self.isVisible():
                return
            self.btn_check_update.setEnabled(True)
            self.btn_check_update.setText(tr("Verificar Atualizações"))
            if has_update:
                res = QMessageBox.question(
                    self,
                    tr("Atualização Disponível"),
                    tr("Uma nova versão do DucksDrive (%s) está disponível.\nSua versão atual é v%s.\n\nDeseja abrir a página de download agora?")
                    % (latest_ver, APP_VERSION),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if res == QMessageBox.StandardButton.Yes:
                    QDesktopServices.openUrl(QUrl(url))
            else:
                QMessageBox.information(
                    self,
                    tr("Atualização"),
                    tr("Você está usando a versão mais recente (v%s).") % APP_VERSION,
                )

        def _on_failed(err_msg: str) -> None:
            if not self.isVisible():
                return
            self.btn_check_update.setEnabled(True)
            self.btn_check_update.setText(tr("Verificar Atualizações"))
            QMessageBox.warning(
                self,
                tr("Atualização"),
                tr("Não foi possível verificar atualizações no momento."),
            )

        worker.check_finished.connect(_on_finished)
        worker.check_failed.connect(_on_failed)
        worker.start()

    def _on_view_logs(self) -> None:
        """Opens diagnostic log viewer dialog non-blockingly."""
        from log_viewer import LogViewerDialog

        if self._log_viewer_dialog is not None and self._log_viewer_dialog.isVisible():
            self._log_viewer_dialog.raise_()
            self._log_viewer_dialog.activateWindow()
            return
        dialog = LogViewerDialog(parent=self)
        self._log_viewer_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_manage_keyring(self) -> None:
        """Prompts user to store, update, or clear the rclone configuration password without freezing the GUI."""
        from keyring_manager import (
            clear_rclone_password,
            get_rclone_password,
            is_keyring_available,
            is_rclone_conf_encrypted,
            store_rclone_password,
        )
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        if not is_keyring_available():
            QMessageBox.warning(
                self,
                tr("Chaveiro Indisponível"),
                tr("O utilitário 'secret-tool' não foi encontrado no sistema.\nInstale 'libsecret' ou 'libsecret-tools' para habilitar a integração com o chaveiro.")
            )
            return

        self.btn_manage_keyring.setEnabled(False)

        def _after_lookup(existing_pwd: Any) -> None:
            if not self.isVisible():
                return
            self.btn_manage_keyring.setEnabled(True)
            has_existing = bool(existing_pwd)
            if has_existing:
                box = QMessageBox(
                    QMessageBox.Icon.Question,
                    tr("Chaveiro do Sistema"),
                    tr("Uma senha do rclone já está armazenada no chaveiro do sistema.\nO que você deseja fazer?"),
                    parent=self,
                )
                btn_update = box.addButton(tr("Atualizar Senha"), QMessageBox.ButtonRole.AcceptRole)
                btn_delete = box.addButton(tr("Remover do Chaveiro"), QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(btn_update)
                box.setEscapeButton(btn_cancel)
                box.exec()
                clicked = box.clickedButton()
                if clicked is btn_delete:
                    self.btn_manage_keyring.setEnabled(False)

                    def _after_clear(ok_cleared: Any) -> None:
                        if not self.isVisible():
                            return
                        self.btn_manage_keyring.setEnabled(True)
                        if ok_cleared:
                            self.keyring_checkbox.setChecked(False)
                            QMessageBox.information(self, tr("Chaveiro do Sistema"), tr("Senha removida do chaveiro com sucesso."))
                        else:
                            QMessageBox.warning(self, tr("Erro"), tr("Não foi possível remover a senha do chaveiro."))

                    self._cache_runner.submit(clear_rclone_password, callback=_after_clear)
                    return
                elif clicked is not btn_update:
                    return

            pwd, ok = QInputDialog.getText(
                self,
                tr("Senha do Rclone no Chaveiro"),
                tr("Digite a senha de criptografia da configuração do rclone:"),
                QLineEdit.EchoMode.Password,
            )
            if ok and pwd:
                self.btn_manage_keyring.setEnabled(False)

                def _store_and_check(secret: str) -> tuple[bool, bool]:
                    return store_rclone_password(secret), is_rclone_conf_encrypted()

                def _after_store(res: Any) -> None:
                    if not self.isVisible():
                        return
                    self.btn_manage_keyring.setEnabled(True)
                    stored, is_encrypted = res if isinstance(res, tuple) else (False, False)
                    if stored:
                        msg = tr("Senha gravada com segurança no chaveiro do sistema!\nO DucksDrive usará '--password-command' automaticamente.")
                        if not is_encrypted:
                            msg += "\n\n" + tr("Aviso: seu arquivo rclone.conf ainda não está criptografado. Para criptografá-lo no disco, execute 'rclone config' no terminal (opção 's' -> Definir senha).")
                        QMessageBox.information(self, tr("Chaveiro do Sistema"), msg)
                        self.keyring_checkbox.setChecked(True)
                    else:
                        QMessageBox.warning(self, tr("Erro"), tr("Falha ao gravar a senha no chaveiro do sistema."))

                self._cache_runner.submit(_store_and_check, pwd, callback=_after_store)

        self._cache_runner.submit(get_rclone_password, callback=_after_lookup)

    def _refresh_cache_usage(self) -> None:
        """Asynchronously calculates local VFS cache usage in a background thread."""
        self.cache_usage_label.setText(tr("Calculando espaço..."))
        self._cache_runner.submit(
            get_vfs_cache_usage_bytes,
            callback=lambda usage: self.cache_usage_label.setText(format_bytes(int(usage or 0))),
        )

    def _on_clear_cache(self) -> None:
        """Safely clears local VFS cache files in background if no uploads are waiting in queue."""
        self.btn_clear_cache.setEnabled(False)
        active_daemons = self._get_active_daemons()
        creds_list = [d.rc_credentials for d in active_daemons if getattr(d, "rc_credentials", None)]

        def _check_before_clear(rc_creds_list: list[Any]) -> tuple[int, int]:
            usage = get_vfs_cache_usage_bytes()
            pending = 0
            if usage > 0:
                for rc_creds in rc_creds_list:
                    try:
                        q_count = int(fetch_vfs_queue(rc_creds) or 0)
                        st = fetch_rclone_stats(rc_creds, timeout=2.0)
                        active_count = len(st.active_transfers) if (st and st.is_online) else 0
                        pending += max(q_count, active_count)
                    except Exception:
                        pass
            return usage, pending

        self._cache_runner.submit(_check_before_clear, creds_list, callback=self._on_pre_clear_checked)

    def _on_pre_clear_checked(self, result: Any) -> None:
        if not isinstance(result, tuple):
            self.btn_clear_cache.setEnabled(True)
            return
        usage, pending = result
        self.cache_usage_label.setText(format_bytes(usage))

        if usage <= 0:
            self.btn_clear_cache.setEnabled(True)
            QMessageBox.information(self, tr("Limpar Cache VFS"), tr("O cache local VFS já está vazio."))
            return

        if pending > 0:
            self.btn_clear_cache.setEnabled(True)
            QMessageBox.warning(
                self,
                tr("Cache VFS em Uso"),
                tr(
                    "O cache não pode ser limpo enquanto houver arquivos aguardando envio para a nuvem.\n"
                    "Existem %d arquivo(s) na fila ou em transferência ativa.\n"
                    "Aguarde o término dos envios para evitar perda de dados."
                ) % pending,
            )
            return

        res = QMessageBox.question(
            self,
            tr("Limpar Cache VFS"),
            tr("Deseja remover %s de arquivos temporários em cache no SSD?\n(Seus arquivos na nuvem não serão afetados.)") % format_bytes(usage),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            self.btn_clear_cache.setEnabled(True)
            return

        self._clearing_cache = True

        def _do_clear() -> int:
            clear_vfs_cache_files()
            return get_vfs_cache_usage_bytes()

        active_daemons = self._get_active_daemons()
        if active_daemons:
            remount_targets: list[tuple[Any, str, bool, Any]] = []
            for d in active_daemons:
                rem_name = d.current_remote
                if rem_name:
                    remount_targets.append((
                        d,
                        rem_name,
                        bool(getattr(d, "current_read_only", False)),
                        getattr(d, "current_extra_args", None),
                    ))

            remaining_to_stop = len(active_daemons)

            def _after_all_stopped() -> None:
                def _on_cleared_remount(remaining: Any) -> None:
                    for d_item, r_name, ro, extra in remount_targets:
                        d_item.start(r_name, extra_args=extra, read_only=ro)
                    self._on_cache_cleared(remaining)

                self._cache_runner.submit(_do_clear, callback=_on_cleared_remount)

            def _on_one_stopped() -> None:
                nonlocal remaining_to_stop
                remaining_to_stop -= 1
                if remaining_to_stop <= 0:
                    _after_all_stopped()

            for d in active_daemons:
                d.stop_async(on_done=_on_one_stopped)
        else:
            self._cache_runner.submit(_do_clear, callback=self._on_cache_cleared)

    def _on_cache_cleared(self, remaining_bytes: Any) -> None:
        self._clearing_cache = False
        self.cache_usage_label.setText(format_bytes(int(remaining_bytes or 0)))
        self.btn_clear_cache.setEnabled(True)
        if self.isVisible():
            QMessageBox.information(self, tr("Cache Limpo"), tr("O cache VFS local foi limpo com sucesso."))

    def done(self, r: int) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            try:
                self._update_worker.check_finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                self._update_worker.check_failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            if not self._update_worker.wait(1500):
                # Detach parent so closing the dialog never triggers QThread destruction abort
                self._update_worker.setParent(QApplication.instance())
                self._update_worker.finished.connect(self._update_worker.deleteLater)
        self._update_worker = None

        if self._clearing_cache:
            self._cache_runner.wait_all(10000, run_queued=True)
        else:
            self._cache_runner.wait_all(2000, run_queued=False)
        super().done(r)

    def _save_and_accept(self) -> None:
        """Validates controls, warns on high SSD cache size, saves to config, and closes."""
        profile = self.profile_combo.currentData() or "balanced"
        bwlimit = self.bwlimit_combo.currentData() or "off"
        transfers = self.transfers_spin.value()
        cache_gb = self.cache_spin.value()
        cache_size = f"{cache_gb}G"
        cache_age = self.cache_age_combo.currentData() or "24h"
        vfs_mode = self.vfs_mode_combo.currentData() or "full"
        language = self.lang_combo.currentData() or "pt_BR"
        theme = self.theme_combo.currentData() or "auto"
        autostart = self.autostart_checkbox.isChecked()
        use_keyring = self.keyring_checkbox.isChecked()
        language_changed = language != str(self.config.get("language", "pt_BR"))
        theme_changed = theme != str(self.config.get("theme", "auto"))
        bwlimit_changed = bwlimit != str(self.config.get("bwlimit", "off"))
        mount_opts_changed = (
            cache_size != str(self.config.get("vfs_cache_max_size", "2G"))
            or cache_age != str(self.config.get("vfs_cache_max_age", "24h"))
            or profile != str(self.config.get("performance_profile", "balanced"))
            or int(transfers) != int(self.config.get("transfers", 4))
            or vfs_mode != str(self.config.get("vfs_cache_mode", "full"))
            or use_keyring != bool(self.config.get("use_system_keyring", False))
        )

        # Validate high cache size warning if >= 50 GB
        if self.cache_spin.isEnabled() and cache_gb >= 50:
            res = QMessageBox.warning(
                self,
                tr("Aviso de Cache Elevado"),
                tr("Atenção: Um tamanho de cache VFS de %s GB é muito elevado e corre o risco de esgotar o espaço do seu SSD!\n\nDeseja prosseguir?") % cache_gb,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return

        # Update config dict
        self.config["performance_profile"] = profile
        self.config["bwlimit"] = bwlimit
        self.config["vfs_cache_max_size"] = cache_size
        self.config["vfs_cache_max_age"] = cache_age
        self.config["transfers"] = transfers
        self.config["vfs_cache_mode"] = vfs_mode
        self.config["autostart"] = autostart
        self.config["use_system_keyring"] = use_keyring
        self.config["language"] = language
        self.config["theme"] = theme

        # Save to disk
        if save_config(self.config):
            self.language_changed = language_changed
            self.theme_changed = theme_changed
            self.bwlimit_changed = bwlimit_changed
            self.apply_theme(theme)
            if language_changed:
                set_language(language)
            if mount_opts_changed:
                QMessageBox.information(
                    self,
                    tr("Configuração Guardada"),
                    tr("Preferências atualizadas com sucesso.\nAs alterações de cache/transferências serão aplicadas na próxima conexão.")
                )
            else:
                QMessageBox.information(
                    self,
                    tr("Configuração Guardada"),
                    tr("Preferências atualizadas e aplicadas imediatamente.")
                )
            self.accept()
        else:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao gravar o ficheiro de configuração."))

    def _on_uninstall(self) -> None:
        """Asks for confirmation, stops active mounts, removes all app/rclone configs, caches, shortcuts, and quits."""
        if os.path.exists("/.flatpak-info"):
            cmd_uninstall = "flatpak uninstall --delete-data com.github.patopatetico2.ducksdrive"
            box = QMessageBox(
                QMessageBox.Icon.Information,
                tr("Desinstalar DucksDrive"),
                tr(
                    "O DucksDrive está sendo executado como pacote Flatpak.\n\n"
                    "Devido ao isolamento da sandbox, a desinstalação deve ser concluída pelo terminal do sistema com o comando:\n\n"
                    "%s"
                ) % cmd_uninstall,
                parent=self,
            )
            btn_copy_exit = box.addButton(tr("Copiar Comando e Sair"), QMessageBox.ButtonRole.AcceptRole)
            btn_cancel = box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_copy_exit)
            box.setEscapeButton(btn_cancel)
            box.exec()

            if box.clickedButton() is btn_copy_exit:
                clipboard = QApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(cmd_uninstall)
                for d in self._get_active_daemons():
                    try:
                        d.stop()
                    except Exception:
                        pass
                self.reject()
                QApplication.quit()
            return

        res = QMessageBox.question(
            self,
            tr("Desinstalar DucksDrive"),
            tr("Tem a certeza de que deseja remover todas as configurações, montagens, atalhos e ficheiros da aplicação do DucksDrive?\n\nA aplicação será encerrada e desinstalada."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res != QMessageBox.Yes:
            return

        import subprocess
        import urllib.parse
        from filemanager_integration import _atomic_write_text, clean_integration
        from rclone_daemon import unmount_fuse_path, is_path_mounted
        from rclone_config import get_rclone_conf_path

        # 1. Cleanly stop all running daemons first so rclone exits and releases mount points and log files
        for d in self._get_known_daemons():
            try:
                if hasattr(d, "health_timer") and d.health_timer:
                    d.health_timer.stop()
                if d.is_running() or (hasattr(d, "is_process_alive") and d.is_process_alive()) or getattr(d, "state", "") in ("starting", "unmounting"):
                    d.stop()
            except Exception:
                pass

        # Ensure any asynchronous sidebar/cache cleanup tasks queued on stop() have completed
        self._cache_runner.wait_all(3000)
        if self.runner is not None:
            try:
                self.runner.wait_all(5000)
            except Exception:
                pass
        for d in self._get_known_daemons():
            daemon_runner = getattr(d, "_integration_runner", None)
            if daemon_runner is not None and daemon_runner is not self.runner:
                try:
                    daemon_runner.wait_all(5000)
                except Exception:
                    pass

        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        xdg_bin = os.environ.get("XDG_BIN_HOME", os.path.expanduser("~/.local/bin"))

        autostart_file = os.path.join(xdg_config, "autostart", "ducksdrive.desktop")
        config_dir = CONFIG_DIR
        rclone_config_dir = os.path.join(xdg_config, "rclone")
        rclone_conf_file = get_rclone_conf_path()
        rclone_cache_dir = os.path.join(xdg_cache, "rclone")
        app_share_dir = os.path.join(xdg_data, "ducksdrive")
        desktop_file = os.path.join(xdg_data, "applications", "ducksdrive.desktop")
        bin_file = os.path.join(xdg_bin, "ducksdrive")
        appimage_path = os.environ.get("APPIMAGE")

        wipe_rclone = False
        if (rclone_conf_file and os.path.exists(rclone_conf_file)) or os.path.isdir(rclone_cache_dir):
            rc_res = QMessageBox.question(
                self,
                tr("Remover Configurações do Rclone?"),
                tr("Deseja também apagar todas as nuvens configuradas no Rclone (%s) e o cache VFS?\n\nEscolha 'Não' se você utiliza o Rclone em outros programas ou scripts.") % rclone_conf_file,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            wipe_rclone = (rc_res == QMessageBox.Yes)

        # 2. Unmount and remove all directories under ~/CloudDrives and custom per-remote mount points
        cloud_drives_base = os.path.expanduser("~/CloudDrives")
        if os.path.isdir(cloud_drives_base):
            for item in os.listdir(cloud_drives_base):
                mp = os.path.join(cloud_drives_base, item)
                if os.path.isdir(mp):
                    unmount_fuse_path(mp, lazy=True)
                    if not is_path_mounted(mp):
                        try:
                            os.rmdir(mp)
                        except OSError:
                            pass
            try:
                os.rmdir(cloud_drives_base)
            except OSError:
                pass

        remotes_cfg = self.config.get("remotes_config")
        if isinstance(remotes_cfg, dict):
            for r_cfg in remotes_cfg.values():
                if not isinstance(r_cfg, dict):
                    continue
                mp_raw = str(r_cfg.get("custom_mount_point") or r_cfg.get("mount_path") or "").strip()
                if not mp_raw:
                    continue
                mp = os.path.abspath(os.path.expanduser(mp_raw))
                if os.path.isdir(mp) or is_path_mounted(mp):
                    unmount_fuse_path(mp, lazy=True)
                try:
                    clean_integration(mp)
                except Exception:
                    pass

        # 3. Remove orphaned GUI bookmarks and .bak files
        cloud_drives_uri = f"file://{urllib.parse.quote(os.path.abspath(cloud_drives_base))}"
        legacy_gdrive_uri = f"file://{urllib.parse.quote(os.path.abspath(os.path.expanduser('~/GoogleDrive')))}"

        for p in [
            os.path.join(xdg_data, "user-places.xbel"),
            os.path.join(xdg_config, "user-places.xbel"),
        ]:
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        s = f.read()
                    new_s = re.sub(r'<bookmark[^>]*href="file://[^"]*CloudDrives[^"]*".*?</bookmark>\s*', '', s, flags=re.DOTALL)
                    new_s = re.sub(r'<separator>(?:(?!</separator>).)*?CloudDrives.*?</separator>\s*', '', new_s, flags=re.DOTALL)
                    if new_s != s:
                        _atomic_write_text(p, new_s)
                except Exception:
                    pass
            bak_p = f"{p}.bak"
            if os.path.exists(bak_p):
                try:
                    os.remove(bak_p)
                except OSError:
                    pass

        gtk_bookmarks = os.path.join(xdg_config, "gtk-3.0", "bookmarks")
        if os.path.exists(gtk_bookmarks):
            try:
                with open(gtk_bookmarks, encoding="utf-8") as f:
                    lines = f.readlines()
                kept = []
                for line in lines:
                    stripped = line.strip()
                    uri_part = stripped.split(" ", 1)[0] if stripped else ""
                    is_ducksdrive_uri = (
                        uri_part == cloud_drives_uri
                        or uri_part.startswith(cloud_drives_uri + "/")
                        or uri_part == legacy_gdrive_uri
                    )
                    if not is_ducksdrive_uri:
                        kept.append(line)
                if len(kept) != len(lines):
                    _atomic_write_text(gtk_bookmarks, "".join(kept))
            except Exception:
                pass

        # 4. Detach and close file logging handlers before deleting app_share_dir
        for log_name in (None, "ducksdrive"):
            lg = logging.getLogger(log_name)
            for handler in list(lg.handlers):
                if isinstance(handler, logging.FileHandler):
                    lg.removeHandler(handler)
                    try:
                        handler.close()
                    except Exception:
                        pass

        # 5. Remove all application, configuration, and cache files
        try:
            if os.path.exists(autostart_file):
                os.remove(autostart_file)
            if os.path.isdir(config_dir):
                shutil.rmtree(config_dir)
            if wipe_rclone:
                for rc_file in (rclone_conf_file, f"{rclone_conf_file}.bak"):
                    if rc_file and os.path.exists(rc_file):
                        try:
                            os.remove(rc_file)
                        except OSError:
                            pass
                if os.path.isdir(rclone_config_dir):
                    shutil.rmtree(rclone_config_dir, ignore_errors=True)
                if os.path.isdir(rclone_cache_dir):
                    shutil.rmtree(rclone_cache_dir, ignore_errors=True)
            if os.path.isdir(app_share_dir):
                shutil.rmtree(app_share_dir)
            if os.path.exists(desktop_file):
                os.remove(desktop_file)
                update_db_bin = shutil.which("update-desktop-database")
                if update_db_bin:
                    subprocess.run([update_db_bin, os.path.dirname(desktop_file)], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            if os.path.exists(bin_file):
                os.remove(bin_file)
            if appimage_path and os.path.exists(appimage_path):
                os.remove(appimage_path)
        except Exception as e:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao remover alguns ficheiros:") + f"\n{e}")
            return

        is_system_pkg = (
            os.path.exists("/usr/bin/ducksdrive")
            or os.path.exists("/.flatpak-info")
            or bool(os.environ.get("FLATPAK_ID"))
        )
        if is_system_pkg and not appimage_path:
            QMessageBox.information(
                self,
                tr("Desinstalar DucksDrive"),
                tr("Dados e montagens do usuário foram removidos.\nComo o pacote foi instalado no sistema, remova-o pelo gerenciador de pacotes da sua distribuição (ex: apt, pacman, dnf ou flatpak).")
            )

        self.reject()
        QApplication.quit()
