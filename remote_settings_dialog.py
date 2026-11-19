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
import re
import shlex
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
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
from rclone_config import (
    get_drive_client_credentials,
    get_remote_type_fast,
    update_drive_client_credentials,
)
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
        self._initial_drive_creds = (
            get_drive_client_credentials(self.remote_name) if self.remote_type == "drive" else ("", "")
        )
        self.saved = False

        self.setWindowTitle(tr("Configurações da Nuvem: %s") % self.remote_name)
        self.setMinimumWidth(540)

        self._init_ui()
        self._load_values()
        self.apply_theme(str(load_config().get("theme") or "auto"))

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies the active QPalette styling to the dialog and its child widgets."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
            for child in self.findChildren(QWidget):
                child.setPalette(palette)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

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

        # Group 2b: Google Drive Custom OAuth Client ID (Anti Rate-Limit)
        if self.remote_type == "drive":
            oauth_group = QGroupBox(tr("Credenciais OAuth Próprias do Google Drive (Anti Rate-Limit)"))
            oauth_layout = QVBoxLayout(oauth_group)
            oauth_layout.setSpacing(8)

            self.drive_oauth_status_label = QLabel()
            self.drive_oauth_status_label.setWordWrap(True)
            oauth_layout.addWidget(self.drive_oauth_status_label)

            oauth_form = QFormLayout()
            oauth_form.setSpacing(8)
            self.client_id_edit = QLineEdit()
            self.client_id_edit.setPlaceholderText("ex: 123456789-abc.apps.googleusercontent.com")
            self.client_secret_edit = QLineEdit()
            self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.client_secret_edit.setPlaceholderText("ex: GOCSPX-...")
            oauth_form.addRow(tr("Google Client ID:"), self.client_id_edit)
            oauth_form.addRow(tr("Google Client Secret:"), self.client_secret_edit)
            oauth_layout.addLayout(oauth_form)

            guide_row = QHBoxLayout()
            guide_row.addStretch()
            self.btn_client_id_guide = QPushButton(tr("Como criar meu Client ID..."))
            self.btn_client_id_guide.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl("https://rclone.org/drive/#making-your-own-client-id"))
            )
            guide_row.addWidget(self.btn_client_id_guide)
            oauth_layout.addLayout(guide_row)
            main_layout.addWidget(oauth_group)
        else:
            self.drive_oauth_status_label = None
            self.client_id_edit = None
            self.client_secret_edit = None
            self.btn_client_id_guide = None

        # Group 3: Performance Overrides (Optional)
        perf_group = QGroupBox(tr("Substituições de Desempenho (Opcional)"))
        perf_layout = QFormLayout(perf_group)
        perf_layout.setSpacing(10)

        self.profile_combo = QComboBox()
        self.profile_combo.addItem(tr("Herdar configuração global"), "")
        self.profile_combo.addItem(tr("Equilibrado (Dolphin Rápido + Multi-Stream)"), "balanced")
        self.profile_combo.addItem(tr("Downloads Grandes e Streaming (Buffer 32M / Read-Ahead 64M)"), "streaming")
        self.profile_combo.addItem(tr("Upload Seguro (Evita Saturar a Rede / Bufferbloat)"), "safe_upload")
        perf_layout.addRow(tr("Perfil de Desempenho:"), self.profile_combo)

        self.bwlimit_combo = QComboBox()
        self.bwlimit_combo.addItem(tr("Herdar configuração global"), "")
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
            try:
                tokens = shlex.split(flags)
            except ValueError:
                tokens = flags.split()
            self.shared_with_me_check.setChecked("--drive-shared-with-me" in tokens)

        if self.client_id_edit is not None and self.client_secret_edit is not None:
            cid, csec = self._initial_drive_creds
            self.client_id_edit.setText(cid)
            self.client_secret_edit.setText(csec)
            self._refresh_drive_oauth_status_badge(bool(cid))

        prof = str(self.remote_cfg.get("performance_profile") or "")
        p_idx = self.profile_combo.findData(prof)
        if p_idx >= 0:
            self.profile_combo.setCurrentIndex(p_idx)

        bw = str(self.remote_cfg.get("bwlimit") or "")
        idx = self.bwlimit_combo.findData(bw)
        if idx >= 0:
            self.bwlimit_combo.setCurrentIndex(idx)
        elif bw:
            self.bwlimit_combo.addItem(tr("Personalizado (%s)") % bw, bw)
            self.bwlimit_combo.setCurrentIndex(self.bwlimit_combo.count() - 1)

        cache_str = str(self.remote_cfg.get("vfs_cache_max_size") or "").strip().upper()
        cache_gb = 0
        if cache_str:
            m = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT])?I?B?$", cache_str)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                if unit == "K":
                    cache_gb = max(1, int(round(val / (1024.0 * 1024.0))))
                elif unit == "M":
                    cache_gb = max(1, int(round(val / 1024.0)))
                elif unit == "T":
                    cache_gb = int(round(val * 1024.0))
                else:
                    cache_gb = int(round(val))
        self.cache_spin.setValue(max(0, min(cache_gb, 500)))

    def _refresh_drive_oauth_status_badge(self, has_custom: bool) -> None:
        if self.drive_oauth_status_label is None:
            return
        if has_custom:
            self.drive_oauth_status_label.setText(
                tr("✓ Client ID próprio configurado (Pacer rápido ativo: 10ms / Chunks 64M).")
            )
            self.drive_oauth_status_label.setStyleSheet(
                "font-size: 8.5pt; font-weight: bold; color: #27ae60;"
            )
        else:
            self.drive_oauth_status_label.setText(
                tr(
                    "⚠️ Usando Client ID compartilhado do Rclone (sujeito a pausas de 30-50s por rate-limit da API e descontinuação em 2026). Configure seu próprio Client ID abaixo para máxima velocidade."
                )
            )
            self.drive_oauth_status_label.setStyleSheet(
                "font-size: 8.5pt; font-weight: bold; color: #e67e22;"
            )

    def _toggle_shared_with_me(self, checked: bool) -> None:
        current_flags = self.flags_edit.text()
        flag = "--drive-shared-with-me"
        try:
            parts = shlex.split(current_flags)
            use_shlex_join = True
        except ValueError:
            parts = current_flags.split()
            use_shlex_join = False
        if checked:
            if flag not in parts:
                parts.append(flag)
        else:
            parts = [p for p in parts if p != flag]
        self.flags_edit.setText(shlex.join(parts) if use_shlex_join else " ".join(parts))

    def _on_browse_mount_point(self) -> None:
        initial = self.mount_path_edit.text().strip() or os.path.expanduser(f"~/CloudDrives/{self.remote_name}")
        path = QFileDialog.getExistingDirectory(self, tr("Selecionar Ponto de Montagem"), initial)
        if path:
            self.mount_path_edit.setText(path)

    def _on_save(self) -> None:
        custom_mp = self.mount_path_edit.text().strip()
        if custom_mp:
            custom_mp = os.path.abspath(os.path.expanduser(custom_mp))
            home_dir = os.path.abspath(os.path.expanduser("~"))
            if custom_mp in ("/", home_dir):
                QMessageBox.warning(
                    self,
                    tr("Ponto de Montagem Inválido"),
                    tr("Não é seguro montar uma nuvem diretamente sobre '%s'. Escolha uma subpasta vazia dedicada.") % custom_mp,
                )
                return
            from rclone_daemon import is_path_mounted
            if os.path.isdir(custom_mp) and not is_path_mounted(custom_mp):
                try:
                    if os.listdir(custom_mp):
                        res = QMessageBox.warning(
                            self,
                            tr("Pasta Não Vazia"),
                            tr("A pasta '%s' já contém arquivos.\nMontar a nuvem sobre ela ocultará esses arquivos locais enquanto o drive estiver montado.\n\nDeseja continuar mesmo assim?") % custom_mp,
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No,
                        )
                        if res != QMessageBox.StandardButton.Yes:
                            return
                except OSError:
                    pass

        read_only = self.ro_checkbox.isChecked()
        flags = self.flags_edit.text().strip()
        if flags:
            try:
                shlex.split(flags)
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    tr("Erro de Input"),
                    tr("As flags personalizadas contêm aspas inválidas ou sintaxe incorreta:\n%s") % str(exc),
                )
                return
        bw = str(self.bwlimit_combo.currentData() or "")
        prof = str(self.profile_combo.currentData() or "")
        cache_gb = self.cache_spin.value()
        cache_str = f"{cache_gb}G" if cache_gb > 0 else ""

        if self.client_id_edit is not None and self.client_secret_edit is not None:
            new_cid = self.client_id_edit.text().strip()
            new_csec = self.client_secret_edit.text().strip()
            if (new_cid, new_csec) != self._initial_drive_creds:
                if bool(new_cid) != bool(new_csec):
                    QMessageBox.warning(
                        self,
                        tr("Credenciais Incompletas"),
                        tr("Informe tanto o Google Client ID quanto o Client Secret (ou deixe ambos em branco)."),
                    )
                    return
                ok_upd, msg_upd = update_drive_client_credentials(self.remote_name, new_cid, new_csec)
                if not ok_upd:
                    QMessageBox.warning(self, tr("Erro ao Atualizar Client ID"), msg_upd)
                    return
                self._initial_drive_creds = (new_cid, new_csec)

        updated_cfg = {
            "custom_mount_point": custom_mp,
            "read_only": read_only,
            "custom_flags": flags,
            "bwlimit": bw,
            "vfs_cache_max_size": cache_str,
            "performance_profile": prof,
        }

        if save_remote_config(self.remote_name, updated_cfg):
            self.saved = True
            self.remote_cfg = updated_cfg
            self.accept()
        else:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao gravar o ficheiro de configuração."))
