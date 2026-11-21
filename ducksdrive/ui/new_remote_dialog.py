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
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ducksdrive.core.rclone_config import SUPPORTED_REMOTE_TYPES
from ducksdrive.core.rclone_settings import apply_app_theme, load_config
from ducksdrive.i18n import tr


class NewRemoteDialog(QDialog):
    """Dialog to gather details for creating a new rclone remote with conditional parameters for non-OAuth providers."""

    def __init__(self, parent: Optional[QWidget] = None, existing_names: Optional[list[str]] = None) -> None:
        super().__init__(parent)
        # Names already configured, listed by the caller in the background (no subprocess here)
        self._existing_names = list(existing_names or [])
        self.setWindowTitle(tr("Configurar Nova Nuvem"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(tr("ex: meu_drive"))

        self.type_combo = QComboBox()
        for provider in SUPPORTED_REMOTE_TYPES:
            self.type_combo.addItem(provider["label"], provider["type"])
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

        # S3 Fields
        self.s3_key_input = QLineEdit()
        self.s3_key_input.setPlaceholderText("Access Key ID")
        self.s3_secret_input = QLineEdit()
        self.s3_secret_input.setPlaceholderText("Secret Access Key")
        self.s3_secret_input.setEchoMode(QLineEdit.Password)
        self.s3_region_input = QLineEdit()
        self.s3_region_input.setPlaceholderText(tr("Vazio = us-east-1 / auto; ou ex: sa-east-1"))
        self.s3_endpoint_input = QLineEdit()
        self.s3_endpoint_input.setPlaceholderText(tr("Vazio = Amazon S3; ou endpoint MinIO/Wasabi/etc."))
        self.conditional_layout.addRow(tr("S3 Access Key:"), self.s3_key_input)
        self.conditional_layout.addRow(tr("S3 Secret Key:"), self.s3_secret_input)
        self.conditional_layout.addRow(tr("S3 Region:"), self.s3_region_input)
        self.conditional_layout.addRow(tr("S3 Endpoint:"), self.s3_endpoint_input)

        # Mega Fields
        self.mega_user_input = QLineEdit()
        self.mega_user_input.setPlaceholderText("email@example.com")
        self.mega_pass_input = QLineEdit()
        self.mega_pass_input.setPlaceholderText(tr("Password"))
        self.mega_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow(tr("Mega Email:"), self.mega_user_input)
        self.conditional_layout.addRow(tr("Mega Password:"), self.mega_pass_input)

        # FTP / SFTP Fields
        self.ftp_host_input = QLineEdit()
        self.ftp_host_input.setPlaceholderText(tr("ftp.example.com ou ip"))
        self.ftp_user_input = QLineEdit()
        self.ftp_user_input.setPlaceholderText(tr("Username"))
        self.ftp_pass_input = QLineEdit()
        self.ftp_pass_input.setPlaceholderText(tr("Password"))
        self.ftp_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow(tr("Host:"), self.ftp_host_input)
        self.conditional_layout.addRow(tr("User:"), self.ftp_user_input)
        self.conditional_layout.addRow(tr("Password:"), self.ftp_pass_input)

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
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def _on_provider_changed(self, index: int) -> None:
        rtype = self.type_combo.currentData()
        # Hide all conditional rows initially
        self.conditional_layout.setRowVisible(self.webdav_url_input, False)
        self.conditional_layout.setRowVisible(self.s3_key_input, False)
        self.conditional_layout.setRowVisible(self.s3_secret_input, False)
        self.conditional_layout.setRowVisible(self.s3_region_input, False)
        self.conditional_layout.setRowVisible(self.s3_endpoint_input, False)
        self.conditional_layout.setRowVisible(self.mega_user_input, False)
        self.conditional_layout.setRowVisible(self.mega_pass_input, False)
        self.conditional_layout.setRowVisible(self.ftp_host_input, False)
        self.conditional_layout.setRowVisible(self.ftp_user_input, False)
        self.conditional_layout.setRowVisible(self.ftp_pass_input, False)

        if rtype == "webdav":
            self.ftp_pass_input.setPlaceholderText(tr("Password"))
            self.conditional_layout.setRowVisible(self.webdav_url_input, True)
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem WebDAV"))
        elif rtype == "s3":
            self.conditional_layout.setRowVisible(self.s3_key_input, True)
            self.conditional_layout.setRowVisible(self.s3_secret_input, True)
            self.conditional_layout.setRowVisible(self.s3_region_input, True)
            self.conditional_layout.setRowVisible(self.s3_endpoint_input, True)
            self.btn_create.setText(tr("Criar Nuvem S3"))
        elif rtype == "mega":
            self.conditional_layout.setRowVisible(self.mega_user_input, True)
            self.conditional_layout.setRowVisible(self.mega_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem Mega"))
        elif rtype in ("ftp", "sftp"):
            self.ftp_pass_input.setPlaceholderText(
                tr("Opcional se usar chave SSH / ssh-agent") if rtype == "sftp" else tr("Password")
            )
            self.conditional_layout.setRowVisible(self.ftp_host_input, True)
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem %s") % rtype.upper())
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
                tr("O nome da nuvem só pode conter letras, números, '-' e '_'.")
            )
            return

        if name in self._existing_names:
            QMessageBox.warning(self, tr("Erro de Duplicado"), tr("Já existe uma nuvem com o nome '%s'.") % name)
            return

        rtype = self.type_combo.currentData()
        if rtype == "webdav":
            if not (self.webdav_url_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "s3":
            if not (self.s3_key_input.text().strip() and self.s3_secret_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "mega":
            if not (self.mega_user_input.text().strip() and self.mega_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "ftp":
            if not (self.ftp_host_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text().strip("\r\n")):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "sftp":
            if not (self.ftp_host_input.text().strip() and self.ftp_user_input.text().strip()):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return

        super().accept()

    def get_data(self) -> tuple[str, str, dict[str, str]]:
        rtype = self.type_combo.currentData()
        extra_params = {}
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
            # A custom endpoint needs provider "Other"; without one, plain Amazon S3
            extra_params["provider"] = "Other" if endpoint else "AWS"
            if key:
                extra_params["access_key_id"] = key
            if secret:
                extra_params["secret_access_key"] = secret
            if region:
                extra_params["region"] = region
            if endpoint:
                extra_params["endpoint"] = endpoint
        elif rtype == "mega":
            user = self.mega_user_input.text().strip()
            password = self.mega_pass_input.text().strip("\r\n")
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        elif rtype in ("ftp", "sftp"):
            host = self.ftp_host_input.text().strip()
            user = self.ftp_user_input.text().strip()
            password = self.ftp_pass_input.text().strip("\r\n")
            if host:
                extra_params["host"] = host
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        return self.name_input.text().strip().rstrip(":"), rtype, extra_params
