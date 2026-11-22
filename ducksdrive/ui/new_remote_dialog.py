"""
ducksdrive/ui/new_remote_dialog.py - Wizard dialog for creating a new Rclone cloud remote.
"""

import re
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ducksdrive.core.rclone_config import S3_PROVIDER_PRESETS, SUPPORTED_REMOTE_TYPES
from ducksdrive.core.rclone_settings import apply_app_theme, load_config
from ducksdrive.i18n import tr


class NewRemoteDialog(QDialog):
    """Dialog to gather details for creating a new rclone remote with conditional parameters for non-OAuth providers."""

    def __init__(self, parent: Optional[QWidget] = None, existing_names: Optional[list[str]] = None) -> None:
        super().__init__(parent)
        # Names already configured, listed by the caller in the background (no subprocess here)
        self._existing_names = list(existing_names or [])
        self.setWindowTitle(tr("Configurar Nova Nuvem"))
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(tr("ex: meu_drive"))

        self.type_combo = QComboBox()
        for provider in SUPPORTED_REMOTE_TYPES:
            self.type_combo.addItem(tr(provider["label"]), provider["type"])
        self.type_combo.currentIndexChanged.connect(self._on_provider_changed)

        form.addRow(tr("Nome da Nuvem:"), self.name_input)
        form.addRow(tr("Provedor:"), self.type_combo)

        # Conditional input container widget
        self.conditional_container = QWidget()
        self.conditional_layout = QFormLayout(self.conditional_container)
        self.conditional_layout.setContentsMargins(0, 0, 0, 0)

        # WebDAV URL field
        self.webdav_url_input = QLineEdit()
        self.webdav_url_input.setPlaceholderText("https://nextcloud.example.com/remote.php/dav/files/user/")
        self.conditional_layout.addRow(tr("URL WebDAV:"), self.webdav_url_input)

        # S3 Fields + Ready Presets
        self.s3_preset_combo = QComboBox()
        for label, prov_key, region_hint, endpoint_hint in S3_PROVIDER_PRESETS:
            self.s3_preset_combo.addItem(tr(label), prov_key)
        self.s3_preset_combo.currentIndexChanged.connect(self._on_s3_preset_changed)

        self.s3_key_input = QLineEdit()
        self.s3_key_input.setPlaceholderText("Access Key ID")
        self.s3_secret_input = QLineEdit()
        self.s3_secret_input.setPlaceholderText("Secret Access Key")
        self.s3_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.s3_region_input = QLineEdit()
        self.s3_region_input.setPlaceholderText(tr("Vazio = us-east-1 / auto; ou ex: sa-east-1"))
        self.s3_endpoint_input = QLineEdit()
        self.s3_endpoint_input.setPlaceholderText(tr("Vazio = Amazon S3; ou endpoint MinIO/Wasabi/etc."))
        self.conditional_layout.addRow(tr("Preset S3:"), self.s3_preset_combo)
        self.conditional_layout.addRow(tr("S3 Access Key:"), self.s3_key_input)
        self.conditional_layout.addRow(tr("S3 Secret Key:"), self.s3_secret_input)
        self.conditional_layout.addRow(tr("S3 Region:"), self.s3_region_input)
        self.conditional_layout.addRow(tr("S3 Endpoint:"), self.s3_endpoint_input)

        # Backblaze B2 Fields
        self.b2_account_input = QLineEdit()
        self.b2_account_input.setPlaceholderText("keyID (Application Key ID)")
        self.b2_key_input = QLineEdit()
        self.b2_key_input.setPlaceholderText("applicationKey")
        self.b2_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.conditional_layout.addRow(tr("B2 Key ID (Account):"), self.b2_account_input)
        self.conditional_layout.addRow(tr("B2 Application Key:"), self.b2_key_input)

        # Mega Fields
        self.mega_user_input = QLineEdit()
        self.mega_user_input.setPlaceholderText("email@example.com")
        self.mega_pass_input = QLineEdit()
        self.mega_pass_input.setPlaceholderText(tr("Password"))
        self.mega_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.conditional_layout.addRow(tr("Mega Email:"), self.mega_user_input)
        self.conditional_layout.addRow(tr("Mega Password:"), self.mega_pass_input)

        # Proton Drive Fields
        self.proton_user_input = QLineEdit()
        self.proton_user_input.setPlaceholderText("usuario@proton.me")
        self.proton_pass_input = QLineEdit()
        self.proton_pass_input.setPlaceholderText(tr("Password"))
        self.proton_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.proton_2fa_input = QLineEdit()
        self.proton_2fa_input.setPlaceholderText(tr("Opcional (código 2FA de 6 dígitos, se ativado)"))
        self.conditional_layout.addRow(tr("Usuário Proton:"), self.proton_user_input)
        self.conditional_layout.addRow(tr("Senha Proton:"), self.proton_pass_input)
        self.conditional_layout.addRow(tr("Código 2FA (Opcional):"), self.proton_2fa_input)

        # FTP / SFTP / SMB Fields
        self.ftp_host_input = QLineEdit()
        self.ftp_host_input.setPlaceholderText(tr("ftp.example.com ou ip"))
        self.ftp_user_input = QLineEdit()
        self.ftp_user_input.setPlaceholderText(tr("Username"))
        self.ftp_pass_input = QLineEdit()
        self.ftp_pass_input.setPlaceholderText(tr("Password"))
        self.ftp_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.smb_domain_input = QLineEdit()
        self.smb_domain_input.setPlaceholderText("WORKGROUP")
        self.conditional_layout.addRow(tr("Host:"), self.ftp_host_input)
        self.conditional_layout.addRow(tr("User:"), self.ftp_user_input)
        self.conditional_layout.addRow(tr("Password:"), self.ftp_pass_input)
        self.conditional_layout.addRow(tr("Domínio SMB (Opcional):"), self.smb_domain_input)

        # Encrypted Vault (rclone crypt) Fields
        self.crypt_no_base_label = QLabel(
            tr("Configure primeiro uma nuvem base (Google Drive, OneDrive, S3, etc.) antes de criar um cofre criptografado.")
        )
        self.crypt_no_base_label.setWordWrap(True)
        self.crypt_no_base_label.setStyleSheet("color: #e67e22; font-size: 8.5pt; font-weight: bold;")

        self.crypt_base_combo = QComboBox()
        for existing in self._existing_names:
            clean_existing = existing.rstrip(":")
            if clean_existing:
                self.crypt_base_combo.addItem(clean_existing, clean_existing)
        self.crypt_base_combo.setEnabled(self.crypt_base_combo.count() > 0)

        self.crypt_folder_input = QLineEdit()
        self.crypt_folder_input.setText("Cofre")
        self.crypt_folder_input.setPlaceholderText(tr("ex: Cofre ou Backups/Seguro"))

        self.crypt_pass_input = QLineEdit()
        self.crypt_pass_input.setPlaceholderText(tr("Senha forte do cofre"))
        self.crypt_pass_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.crypt_salt_input = QLineEdit()
        self.crypt_salt_input.setPlaceholderText(tr("Opcional (salt extra para maior segurança)"))
        self.crypt_salt_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.crypt_filename_combo = QComboBox()
        self.crypt_filename_combo.addItem(tr("Padrão — criptografa nomes de arquivos e pastas (Recomendado)"), "standard")
        self.crypt_filename_combo.addItem(tr("Ofuscado — nomes ofuscados"), "obfuscate")
        self.crypt_filename_combo.addItem(tr("Desativado — apenas conteúdo criptografado"), "off")

        self.conditional_layout.addRow("", self.crypt_no_base_label)
        self.conditional_layout.addRow(tr("Nuvem Base:"), self.crypt_base_combo)
        self.conditional_layout.addRow(tr("Subpasta do Cofre:"), self.crypt_folder_input)
        self.conditional_layout.addRow(tr("Senha de Criptografia:"), self.crypt_pass_input)
        self.conditional_layout.addRow(tr("Salt (Senha 2 Opcional):"), self.crypt_salt_input)
        self.conditional_layout.addRow(tr("Criptografar Nomes:"), self.crypt_filename_combo)

        form.addRow(self.conditional_container)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_create = QPushButton(tr("Iniciar Autorização no Browser"))
        self.btn_create.setDefault(True)
        self.btn_create.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 8px 14px; border-radius: 4px; font-weight: bold;"
        )
        self.btn_create.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_create)
        layout.addLayout(btn_row)

        self._on_s3_preset_changed(0)
        self._on_provider_changed(0)
        self.apply_theme(str(load_config().get("theme") or "auto"))

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies the active theme palette to the NewRemoteDialog."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
            for child in self.findChildren(QWidget):
                child.setPalette(palette)
            style_obj = self.style()
            if style_obj is not None:
                style_obj.unpolish(self)
                style_obj.polish(self)
            self.update()

    def _on_s3_preset_changed(self, index: int) -> None:
        if index < 0 or index >= len(S3_PROVIDER_PRESETS):
            return
        _label, prov_key, region_hint, endpoint_hint = S3_PROVIDER_PRESETS[index]
        self.s3_region_input.setPlaceholderText(tr(region_hint))
        if endpoint_hint:
            self.s3_endpoint_input.setPlaceholderText(endpoint_hint)
        else:
            self.s3_endpoint_input.setPlaceholderText(tr("Vazio = Amazon S3; ou endpoint MinIO/Wasabi/etc."))
        if prov_key == "Cloudflare" and not self.s3_region_input.text().strip():
            self.s3_region_input.setText("auto")
        elif prov_key == "Wasabi" and not self.s3_endpoint_input.text().strip():
            self.s3_endpoint_input.setText("s3.wasabisys.com")

    def _on_provider_changed(self, index: int) -> None:
        rtype = self.type_combo.currentData()
        all_fields = (
            self.webdav_url_input,
            self.s3_preset_combo,
            self.s3_key_input,
            self.s3_secret_input,
            self.s3_region_input,
            self.s3_endpoint_input,
            self.b2_account_input,
            self.b2_key_input,
            self.mega_user_input,
            self.mega_pass_input,
            self.proton_user_input,
            self.proton_pass_input,
            self.proton_2fa_input,
            self.ftp_host_input,
            self.ftp_user_input,
            self.ftp_pass_input,
            self.smb_domain_input,
            self.crypt_no_base_label,
            self.crypt_base_combo,
            self.crypt_folder_input,
            self.crypt_pass_input,
            self.crypt_salt_input,
            self.crypt_filename_combo,
        )
        for field in all_fields:
            self.conditional_layout.setRowVisible(field, False)

        if rtype == "webdav":
            self.ftp_pass_input.setPlaceholderText(tr("Password"))
            self.conditional_layout.setRowVisible(self.webdav_url_input, True)
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem WebDAV"))
        elif rtype == "s3":
            for f in (
                self.s3_preset_combo,
                self.s3_key_input,
                self.s3_secret_input,
                self.s3_region_input,
                self.s3_endpoint_input,
            ):
                self.conditional_layout.setRowVisible(f, True)
            self.btn_create.setText(tr("Criar Nuvem S3"))
        elif rtype == "b2":
            self.conditional_layout.setRowVisible(self.b2_account_input, True)
            self.conditional_layout.setRowVisible(self.b2_key_input, True)
            self.btn_create.setText(tr("Criar Nuvem Backblaze B2"))
        elif rtype == "mega":
            self.conditional_layout.setRowVisible(self.mega_user_input, True)
            self.conditional_layout.setRowVisible(self.mega_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem Mega"))
        elif rtype == "protondrive":
            self.conditional_layout.setRowVisible(self.proton_user_input, True)
            self.conditional_layout.setRowVisible(self.proton_pass_input, True)
            self.conditional_layout.setRowVisible(self.proton_2fa_input, True)
            self.btn_create.setText(tr("Criar Nuvem Proton Drive"))
        elif rtype in ("ftp", "sftp", "smb"):
            if rtype == "sftp":
                self.ftp_pass_input.setPlaceholderText(tr("Opcional se usar chave SSH / ssh-agent"))
                self.ftp_host_input.setPlaceholderText(tr("ftp.example.com ou ip"))
            elif rtype == "smb":
                self.ftp_pass_input.setPlaceholderText(tr("Opcional para compartilhamento convidado"))
                self.ftp_host_input.setPlaceholderText(tr("nas.local ou 192.168.1.10"))
            else:
                self.ftp_pass_input.setPlaceholderText(tr("Password"))
                self.ftp_host_input.setPlaceholderText(tr("ftp.example.com ou ip"))
            self.conditional_layout.setRowVisible(self.ftp_host_input, True)
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
            if rtype == "smb":
                self.conditional_layout.setRowVisible(self.smb_domain_input, True)
            self.btn_create.setText(tr("Criar Nuvem %s") % rtype.upper())
        elif rtype == "crypt":
            has_base = self.crypt_base_combo.count() > 0
            self.conditional_layout.setRowVisible(self.crypt_no_base_label, not has_base)
            for f in (
                self.crypt_base_combo,
                self.crypt_folder_input,
                self.crypt_pass_input,
                self.crypt_salt_input,
                self.crypt_filename_combo,
            ):
                self.conditional_layout.setRowVisible(f, True)
            self.btn_create.setText(tr("Criar Cofre Criptografado"))
        else:
            self.btn_create.setText(tr("Iniciar Autorização no Browser"))
        self.adjustSize()

    def accept(self) -> None:
        name = self.name_input.text().strip().rstrip(":")
        if not name:
            QMessageBox.warning(self, tr("Erro de Input"), tr("O nome da nuvem não pode estar vazio."))
            return

        if not re.match(r"^[A-Za-z0-9_-]+$", name):
            QMessageBox.warning(
                self,
                tr("Erro de Input"),
                tr("O nome da nuvem só pode conter letras, números, '-' e '_'."),
            )
            return

        if name in self._existing_names:
            QMessageBox.warning(self, tr("Erro de Duplicado"), tr("Já existe uma nuvem com o nome '%s'.") % name)
            return

        rtype = self.type_combo.currentData()
        missing_msg = tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado.")
        if rtype == "webdav":
            if not (self.webdav_url_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "s3":
            if not (self.s3_key_input.text().strip() and self.s3_secret_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "b2":
            if not (self.b2_account_input.text().strip() and self.b2_key_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "mega":
            if not (self.mega_user_input.text().strip() and self.mega_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "protondrive":
            if not (self.proton_user_input.text().strip() and self.proton_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "ftp":
            if not (self.ftp_host_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "sftp":
            if not (self.ftp_host_input.text().strip() and self.ftp_user_input.text().strip()):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "smb":
            if not self.ftp_host_input.text().strip():
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return
        elif rtype == "crypt":
            if self.crypt_base_combo.count() == 0:
                QMessageBox.warning(
                    self,
                    tr("Erro de Input"),
                    tr("Configure primeiro uma nuvem base antes de criar um cofre criptografado."),
                )
                return
            if not self.crypt_pass_input.text().strip("\r\n"):
                QMessageBox.warning(self, tr("Erro de Input"), missing_msg)
                return

        super().accept()

    def get_data(self) -> tuple[str, str, dict[str, str]]:
        rtype = str(self.type_combo.currentData() or "")
        extra_params: dict[str, str] = {}
        if rtype == "webdav":
            url = self.webdav_url_input.text().strip()
            user = self.ftp_user_input.text().strip()
            password = self.ftp_pass_input.text().strip("\r\n")
            extra_params["vendor"] = "other"
            if url:
                extra_params["url"] = url
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        elif rtype == "s3":
            key = self.s3_key_input.text().strip()
            secret = self.s3_secret_input.text().strip("\r\n")
            region = self.s3_region_input.text().strip()
            endpoint = self.s3_endpoint_input.text().strip()
            preset_provider = str(self.s3_preset_combo.currentData() or "AWS")
            # A custom endpoint needs provider "Other" if AWS was left selected; otherwise honor explicit preset
            if preset_provider == "AWS" and endpoint:
                preset_provider = "Other"
            extra_params["provider"] = preset_provider
            if key:
                extra_params["access_key_id"] = key
            if secret:
                extra_params["secret_access_key"] = secret
            if region:
                extra_params["region"] = region
            if endpoint:
                extra_params["endpoint"] = endpoint
        elif rtype == "b2":
            account = self.b2_account_input.text().strip()
            key = self.b2_key_input.text().strip("\r\n")
            if account:
                extra_params["account"] = account
            if key:
                extra_params["key"] = key
        elif rtype == "mega":
            user = self.mega_user_input.text().strip()
            password = self.mega_pass_input.text().strip("\r\n")
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        elif rtype == "protondrive":
            user = self.proton_user_input.text().strip()
            password = self.proton_pass_input.text().strip("\r\n")
            twofa = self.proton_2fa_input.text().strip()
            if user:
                extra_params["username"] = user
            if password:
                extra_params["password"] = password
            if twofa:
                extra_params["2fa"] = twofa
        elif rtype in ("ftp", "sftp", "smb"):
            host = self.ftp_host_input.text().strip()
            user = self.ftp_user_input.text().strip()
            password = self.ftp_pass_input.text().strip("\r\n")
            if host:
                extra_params["host"] = host
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
            if rtype == "smb":
                domain = self.smb_domain_input.text().strip()
                if domain:
                    extra_params["domain"] = domain
        elif rtype == "crypt":
            base_remote = str(
                self.crypt_base_combo.currentData() or self.crypt_base_combo.currentText() or ""
            ).strip().rstrip(":")
            subfolder = self.crypt_folder_input.text().strip().strip("/")
            extra_params["remote"] = f"{base_remote}:{subfolder}" if subfolder else f"{base_remote}:"
            extra_params["password"] = self.crypt_pass_input.text().strip("\r\n")
            salt = self.crypt_salt_input.text().strip("\r\n")
            if salt:
                extra_params["password2"] = salt
            fn_mode = str(self.crypt_filename_combo.currentData() or "standard")
            extra_params["filename_encryption"] = fn_mode
            extra_params["directory_name_encryption"] = "false" if fn_mode == "off" else "true"
        return self.name_input.text().strip().rstrip(":"), rtype, extra_params
