"""
gui_tray.py - PySide6 System Tray Interface and Dialogs.

Provides the primary user interface including the System Tray icon,
context menus, status notifications, and dialogs for remote management.
"""

import logging
import os
import time
from typing import Optional

from i18n import tr
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QIcon,
    QDesktopServices,
    QPainter,
    QColor,
    QBrush,
    QPen,
    QPixmap
)
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
    RemoteCreationThread,
    delete_remote
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


_TRAY_ICON_CACHE: dict[str, QIcon] = {}


def get_tray_icon(state: str = "idle") -> QIcon:
    """
    Returns a dynamic QIcon based on state ('idle', 'syncing', 'error').
    Draws a crisp status badge over the base icon and caches the result.
    """
    if state in _TRAY_ICON_CACHE:
        return _TRAY_ICON_CACHE[state]

    base_icon = get_app_icon()
    pixmap = base_icon.pixmap(64, 64)
    if pixmap.isNull():
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)

    if state in ("syncing", "error"):
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        badge_radius = 11
        center_x = 64 - badge_radius - 2
        center_y = 64 - badge_radius - 2

        # Badge background with white contrast outline
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        if state == "syncing":
            painter.setBrush(QBrush(QColor(41, 128, 185)))  # Blue syncing emblem
        else:
            painter.setBrush(QBrush(QColor(231, 76, 60)))   # Red alert emblem

        painter.drawEllipse(center_x - badge_radius, center_y - badge_radius, badge_radius * 2, badge_radius * 2)

        # Draw glyph inside badge
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(Qt.NoBrush)
        if state == "syncing":
            painter.drawArc(center_x - 5, center_y - 5, 10, 10, 30 * 16, 270 * 16)
        else:
            painter.drawLine(center_x, center_y - 5, center_x, center_y + 1)
            painter.drawPoint(center_x, center_y + 4)

        painter.end()

    icon = QIcon(pixmap)
    _TRAY_ICON_CACHE[state] = icon
    return icon


class NewRemoteDialog(QDialog):
    """Dialog to gather details for creating a new rclone remote with conditional parameters for non-OAuth providers."""
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Configurar Nova Nuvem"))
        self.setMinimumWidth(400)
        
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
        self.conditional_layout.addRow("URL WebDAV:", self.webdav_url_input)

        # S3 Fields
        self.s3_key_input = QLineEdit()
        self.s3_key_input.setPlaceholderText("Access Key ID")
        self.s3_secret_input = QLineEdit()
        self.s3_secret_input.setPlaceholderText("Secret Access Key")
        self.s3_secret_input.setEchoMode(QLineEdit.Password)
        self.s3_endpoint_input = QLineEdit()
        self.s3_endpoint_input.setPlaceholderText("Vazio = Amazon S3; ou endpoint MinIO/Wasabi/etc.")
        self.conditional_layout.addRow("S3 Access Key:", self.s3_key_input)
        self.conditional_layout.addRow("S3 Secret Key:", self.s3_secret_input)
        self.conditional_layout.addRow("S3 Endpoint:", self.s3_endpoint_input)

        # Mega Fields
        self.mega_user_input = QLineEdit()
        self.mega_user_input.setPlaceholderText("email@example.com")
        self.mega_pass_input = QLineEdit()
        self.mega_pass_input.setPlaceholderText("Password")
        self.mega_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow("Mega Email:", self.mega_user_input)
        self.conditional_layout.addRow("Mega Password:", self.mega_pass_input)

        # FTP / SFTP Fields
        self.ftp_host_input = QLineEdit()
        self.ftp_host_input.setPlaceholderText("ftp.example.com ou ip")
        self.ftp_user_input = QLineEdit()
        self.ftp_user_input.setPlaceholderText("Username")
        self.ftp_pass_input = QLineEdit()
        self.ftp_pass_input.setPlaceholderText("Password")
        self.ftp_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow("Host:", self.ftp_host_input)
        self.conditional_layout.addRow("User:", self.ftp_user_input)
        self.conditional_layout.addRow("Password:", self.ftp_pass_input)

        form.addRow(self.conditional_container)
        layout.addLayout(form)
        
        self.btn_create = QPushButton(tr("Iniciar Autorização no Browser"))
        self.btn_create.clicked.connect(self.accept)
        layout.addWidget(self.btn_create)

        self._on_provider_changed(0)

    def _on_provider_changed(self, index: int) -> None:
        rtype = self.type_combo.currentData()
        # Hide all conditional rows initially
        self.conditional_layout.setRowVisible(self.webdav_url_input, False)
        self.conditional_layout.setRowVisible(self.s3_key_input, False)
        self.conditional_layout.setRowVisible(self.s3_secret_input, False)
        self.conditional_layout.setRowVisible(self.s3_endpoint_input, False)
        self.conditional_layout.setRowVisible(self.mega_user_input, False)
        self.conditional_layout.setRowVisible(self.mega_pass_input, False)
        self.conditional_layout.setRowVisible(self.ftp_host_input, False)
        self.conditional_layout.setRowVisible(self.ftp_user_input, False)
        self.conditional_layout.setRowVisible(self.ftp_pass_input, False)

        if rtype == "webdav":
            self.conditional_layout.setRowVisible(self.webdav_url_input, True)
            self.btn_create.setText(tr("Criar Nuvem WebDAV"))
        elif rtype == "s3":
            self.conditional_layout.setRowVisible(self.s3_key_input, True)
            self.conditional_layout.setRowVisible(self.s3_secret_input, True)
            self.conditional_layout.setRowVisible(self.s3_endpoint_input, True)
            self.btn_create.setText(tr("Criar Nuvem S3"))
        elif rtype == "mega":
            self.conditional_layout.setRowVisible(self.mega_user_input, True)
            self.conditional_layout.setRowVisible(self.mega_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem Mega"))
        elif rtype in ("ftp", "sftp"):
            self.conditional_layout.setRowVisible(self.ftp_host_input, True)
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
            self.btn_create.setText(tr("Criar Nuvem %s") % rtype.upper())
        else:
            self.btn_create.setText(tr("Iniciar Autorização no Browser"))
        self.adjustSize()

    def accept(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, tr("Erro de Input"), tr("O nome da nuvem não pode estar vazio."))
            return
        
        existing_remotes = list_remote_names()
        if name in existing_remotes:
            QMessageBox.warning(self, tr("Erro de Duplicado"), tr("Já existe uma nuvem com o nome '%s'.") % name)
            return

        super().accept()
        
    def get_data(self) -> tuple[str, str, dict[str, str]]:
        rtype = self.type_combo.currentData()
        extra_params = {}
        if rtype == "webdav":
            url = self.webdav_url_input.text().strip()
            if url:
                extra_params["url"] = url
        elif rtype == "s3":
            key = self.s3_key_input.text().strip()
            secret = self.s3_secret_input.text()  # secrets are never stripped
            endpoint = self.s3_endpoint_input.text().strip()
            # A custom endpoint needs provider "Other"; without one, plain Amazon S3
            extra_params["provider"] = "Other" if endpoint else "AWS"
            if key:
                extra_params["access_key_id"] = key
            if secret:
                extra_params["secret_access_key"] = secret
            if endpoint:
                extra_params["endpoint"] = endpoint
        elif rtype == "mega":
            user = self.mega_user_input.text().strip()
            password = self.mega_pass_input.text()
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        elif rtype in ("ftp", "sftp"):
            host = self.ftp_host_input.text().strip()
            user = self.ftp_user_input.text().strip()
            password = self.ftp_pass_input.text()
            if host:
                extra_params["host"] = host
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
        return self.name_input.text().strip(), rtype, extra_params


class RcloneTrayIcon(QSystemTrayIcon):
    """Main System Tray application logic."""
    
    request_exit = Signal()

    def __init__(self, daemon: RcloneDaemon, parent: Optional[QWidget] = None) -> None:
        super().__init__(get_tray_icon("idle"), parent)
        self.daemon = daemon
        self.setToolTip(f"{APP_NAME}: Idle")
        self._pending_remote_name: Optional[str] = None
        self.latest_stats: Optional[StatsData] = None
        self._was_syncing = False
        self._current_icon_state = "idle"
        
        # Initialize floating status popup panel
        self.status_popup = StatusPopup(daemon)
        
        self._menu = QMenu()
        
        # Bloco 1 (Topo): Status atual da nuvem (desabilitado)
        self.action_status = QAction("DucksDrive • Disconnected", self)
        self.action_status.setEnabled(False)
        self._menu.addAction(self.action_status)
        
        self._menu.addSeparator()
        
        # Bloco 2 (Ações): Produtividade
        self.action_open = QAction(tr("Abrir Pasta Local"), self)
        self.action_open.triggered.connect(self._open_folder)
        self._menu.addAction(self.action_open)

        self.action_panel = QAction(tr("Abrir Painel de Transferências"), self)
        self.action_panel.triggered.connect(self._show_transfer_panel)
        self._menu.addAction(self.action_panel)

        self.action_connect = QAction(tr("Selecionar Nuvem para Conectar..."), self)
        self.action_connect.triggered.connect(self._show_mount_selector)
        self._menu.addAction(self.action_connect)

        self._menu.addSeparator()
        
        # Bloco 3 (Gerenciamento): Submenu dinâmico de Nuvens
        self.menu_remotes = self._menu.addMenu(tr("Gerenciar Nuvens"))
        self.menu_remotes.aboutToShow.connect(self._populate_remotes_menu)
        self._populate_remotes_menu()

        self.action_settings = QAction(tr("Configurações..."), self)
        self.action_settings.triggered.connect(self._show_settings_dialog)
        self._menu.addAction(self.action_settings)
        
        self._menu.addSeparator()
        
        # Bloco 4 (Fundo): Sair
        self.action_exit = QAction(tr("Sair"), self)
        self.action_exit.triggered.connect(self._confirm_quit)
        self._menu.addAction(self.action_exit)

        self.setContextMenu(self._menu)
        
        self.daemon.state_changed.connect(self._on_state_changed)
        self.daemon.mount_error.connect(self._on_mount_error)
        
        self.activated.connect(self._on_activated)

    def _populate_remotes_menu(self) -> None:
        """Dynamically populates the 'Gerenciar Nuvens' submenu without leaking memory."""
        self.menu_remotes.clear()
        
        remotes = list_remote_names()
        if remotes:
            for remote in remotes:
                is_active = self.daemon.is_running() and self.daemon.current_remote and self.daemon.current_remote.rstrip(":") == remote.rstrip(":")
                label = f"✓ {remote} (" + tr("Ativo") + ")" if is_active else remote
                action = QAction(label, self)
                if is_active:
                    action.setEnabled(False)  # Already connected
                action.triggered.connect(lambda checked=False, r=remote: self.daemon.start(r))
                self.menu_remotes.addAction(action)
            self.menu_remotes.addSeparator()
        else:
            empty_action = QAction(tr("Nenhuma nuvem configurada"), self)
            empty_action.setEnabled(False)
            self.menu_remotes.addAction(empty_action)
            self.menu_remotes.addSeparator()

        self.action_unmount = QAction(tr("Desconectar Atual"), self)
        self.action_unmount.triggered.connect(self.daemon.stop)
        self.action_unmount.setEnabled(self.daemon.is_running())
        self.menu_remotes.addAction(self.action_unmount)

        self.action_new = QAction(tr("Adicionar Nova Nuvem..."), self)
        self.action_new.triggered.connect(self._show_new_remote_dialog)
        self.menu_remotes.addAction(self.action_new)

        self.action_delete = QAction(tr("Remover Nuvem..."), self)
        self.action_delete.triggered.connect(self._show_delete_selector)
        self.menu_remotes.addAction(self.action_delete)

    def _show_settings_dialog(self) -> None:
        """Opens the settings configuration dialog with proper widget parent."""
        dialog = SettingsDialog(parent=None)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.exec()

    def update_tray_icon(self, state: str) -> None:
        """
        Updates the tray icon dynamically based on state ('idle', 'syncing', 'error').
        Only changes the icon if the state actually changes to avoid unnecessary repaints.
        """
        if getattr(self, "_current_icon_state", None) == state:
            return
        self._current_icon_state = state
        self.setIcon(get_tray_icon(state))

    @Slot(str)
    def _on_state_changed(self, state: str) -> None:
        """Updates UI elements based on daemon state."""
        is_mounted = state == "mounted"
        self.action_open.setEnabled(is_mounted)
        
        status_map = {
            "stopped": tr("Desconectado"),
            "starting": tr("Conectando..."),
            "mounted": tr("Conectado"),
            "unmounting": tr("Desconectando..."),
            "error": tr("Erro")
        }
        state_label = status_map.get(state, state.capitalize())
        remote_name = self.daemon.current_remote or "DucksDrive"
        clean_name = remote_name.rstrip(":")
        
        status_header = f"{clean_name} • {state_label}"
        self.action_status.setText(status_header)
        self.setToolTip(status_header)
        
        if is_mounted:
            self.update_tray_icon("idle")
            self.showMessage(APP_NAME, tr("Drive montado com sucesso."), QSystemTrayIcon.Information, 3000)
        elif state == "error":
            self.update_tray_icon("error")
        elif state in ("stopped", "unmounting"):
            self.update_tray_icon("idle")

    @Slot(str)
    def _on_mount_error(self, error: str) -> None:
        now = time.time()
        
        last_time = getattr(self, "_last_error_time", 0)
        last_msg = getattr(self, "_last_error_msg", "")
        
        if error == last_msg and now - last_time < 2:
            return
            
        self._last_error_time = now
        self._last_error_msg = error
        self.update_tray_icon("error")
        QMessageBox.critical(None, tr("Erro de Montagem"), error)

    @Slot(object)
    def update_stats(self, stats: StatsData) -> None:
        """Updates the tray tooltip, menu status, and floating status popup with live telemetry."""
        self.latest_stats = stats
        if stats.is_online:
            has_active = stats.transfers_count > 0 or len(stats.active_transfers) > 0
            
            if has_active:
                self._was_syncing = True
                self.update_tray_icon("syncing")
                if stats.active_transfers:
                    t = stats.active_transfers[0]
                    name = t.get("name", "file")
                    pct = int(t.get("percentage") or 0)
                    text = f"{tr('A enviar:')} {name} ({pct}%) • {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                    self.action_status.setText(text)
                    self.setToolTip(f"{APP_NAME}: {text}")
                else:
                    text = f"{tr('Velocidade:')} {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                    self.action_status.setText(text)
                    self.setToolTip(f"{APP_NAME}: {stats.status_text}")
            else:
                self.update_tray_icon("idle")
                if self._was_syncing:
                    self._was_syncing = False
                    self.showMessage(
                        APP_NAME,
                        tr("Transferências concluídas! Todos os ficheiros foram sincronizados com a nuvem."),
                        QSystemTrayIcon.Information,
                        4000
                    )
                text = f"{tr('Velocidade:')} {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                self.action_status.setText(text)
                self.setToolTip(f"{APP_NAME}: {stats.status_text}")
        else:
            self._was_syncing = False
            self.update_tray_icon("error")
            if self.daemon.state == "mounted":
                self.action_status.setText(tr("Status: Conexão Perdida"))

        # Forward telemetry to floating status popup
        self.status_popup.update_stats(stats)

    def _has_active_transfer(self) -> bool:
        """Checks if there is any active file transfer in progress."""
        if not self.latest_stats or not self.latest_stats.is_online:
            return False
        return self.latest_stats.transfers_count > 0 or len(self.latest_stats.active_transfers) > 0

    def _show_transfer_panel(self) -> None:
        """Shows or toggles the floating status popup panel."""
        if self.status_popup.isVisible():
            self.status_popup.close()
        else:
            self.status_popup.show_near_cursor()

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
                tr("Transferências Ativas"),
                tr("Transferências de arquivos estão em andamento.\nTem certeza que deseja sair e interrompê-las?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return
        self.request_exit.emit()

    def _open_folder(self) -> None:
        """Opens the mount point in the system file manager using QDesktopServices."""
        path = self.daemon.mount_point
        if os.path.exists(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception as e:
                logger.error("Failed to open folder with QDesktopServices: %s", e)
        else:
            QMessageBox.warning(None, tr("Pasta Ausente"), tr("O diretório %s não existe.") % path)

    def _show_delete_selector(self) -> None:
        """Shows a dialog to select and securely delete a configured cloud remote."""
        remotes = list_remote_names()
        if not remotes:
            QMessageBox.information(None, tr("Nenhuma Nuvem"), tr("Não existem nuvens configuradas para remover."))
            return

        remote, ok = QInputDialog.getItem(
            None, tr("Remover Nuvem"), tr("Selecione a nuvem que deseja remover:"), remotes, 0, False
        )
        if ok and remote:
            res = QMessageBox.warning(
                None,
                tr("Confirmar Exclusão"),
                tr("Tem certeza que deseja excluir as configurações de '%s'?\nEsta ação não pode ser desfeita.") % remote,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return

            # Security logic: if active, stop daemon first
            active_remote = self.daemon.current_remote
            if active_remote and active_remote.rstrip(":") == remote.rstrip(":"):
                logger.info("Active remote '%s' is being deleted. Stopping daemon...", remote)
                self.daemon.stop()

            if delete_remote(remote):
                QMessageBox.information(None, tr("Nuvem Removida"), tr("A nuvem '%s' foi removida com sucesso.") % remote)
            else:
                QMessageBox.critical(None, tr("Erro"), tr("Falha ao remover a nuvem '%s'.") % remote)

    def _show_mount_selector(self) -> None:
        """Shows a dialog to select which remote to mount."""
        remotes = list_remote_names()
        if not remotes:
            msg = tr("Nenhuma nuvem configurada.\nGostaria de configurar uma agora?")
            res = QMessageBox.question(None, tr("Nenhuma Nuvem"), msg, QMessageBox.Yes | QMessageBox.No)
            if res == QMessageBox.Yes:
                self._show_new_remote_dialog()
            return

        if self.daemon.is_running():
            res = QMessageBox.question(
                None,
                tr("Substituir Drive Ativo"),
                tr("Um drive já está conectado (%s). Deseja desconectá-lo e conectar ao novo remoto configurado?") % self.daemon.current_remote,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return
            
        remote, ok = QInputDialog.getItem(
            None, tr("Conectar Nuvem"), tr("Selecione uma nuvem para conectar:"), remotes, 0, False
        )
        if ok and remote:
            self.daemon.start(remote)

    def _show_new_remote_dialog(self) -> None:
        """Handles the flow for creating a new remote via background thread."""
        dialog = NewRemoteDialog(parent=None)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.Accepted:
            name, rtype, extra_params = dialog.get_data()
            if not name:
                QMessageBox.warning(None, "Input Error", "Remote name cannot be empty.")
                return

            self._pending_remote_name = name
            self._creation_thread = RemoteCreationThread(name, rtype, extra_params=extra_params)
            
            is_oauth = rtype in ("drive", "onedrive", "dropbox", "box", "pcloud")
            msg = (tr("Por favor, complete a autorização no seu navegador para '%s'...") % name) if is_oauth else (tr("Configurando nuvem '%s'...") % name)
            
            # Show a progress message since this can take time
            progress = QMessageBox(QMessageBox.Information, tr("Configurando"), msg, QMessageBox.Cancel, parent=None)
            self._progress_box = progress
            # Cancel button and the window "X" both end up in rejected()
            progress.rejected.connect(self._creation_thread.cancel)
            
            self._creation_thread.finished_creation.connect(self._close_progress)
            self._creation_thread.finished_creation.connect(self._on_creation_finished)
            
            self._creation_thread.start()
            progress.exec()

    @Slot(bool, str)
    def _close_progress(self, success: bool, message: str) -> None:
        """Closes the progress box without letting close() trigger rejected() -> cancel()."""
        progress = getattr(self, "_progress_box", None)
        if progress is None:
            return
        self._progress_box = None
        try:
            progress.rejected.disconnect(self._creation_thread.cancel)
        except (RuntimeError, TypeError):
            pass
        progress.close()

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

                if self.daemon.is_running():
                    res = QMessageBox.question(
                        None,
                        tr("Substituir Drive Ativo"),
                        tr("Um drive já está conectado (%s). Deseja desconectá-lo e conectar ao novo remoto configurado?") % self.daemon.current_remote,
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    if res != QMessageBox.Yes:
                        return
                    self.daemon.stop()

                # Add a 1-second QTimer delay to ensure rclone config file flush completes before mounting
                QTimer.singleShot(1000, lambda: self.daemon.start(remote_name))
        else:
            self._pending_remote_name = None
            if "cancelled" not in message.lower():
                QMessageBox.critical(None, tr("Falha na Configuração"), message)
