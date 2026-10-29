"""
gui_tray.py - PySide6 System Tray Interface and Dialogs.

Provides the primary user interface including the System Tray icon,
context menus, status notifications, and dialogs for remote management.
"""

import logging
import os
import re
import time
from typing import Callable, Optional

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
    QProgressDialog,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QComboBox
)

from rclone_config import (
    SUPPORTED_REMOTE_TYPES,
    TaskRunner,
    list_remote_names,
    RemoteCreationThread,
    delete_remote
)
from rclone_daemon import RcloneDaemon, is_path_mounted
from rclone_settings import load_config, save_last_remote
from rclone_stats import StatsData, fetch_vfs_queue, set_rc_bwlimit
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
    def __init__(self, parent: Optional[QWidget] = None, existing_names: Optional[list[str]] = None) -> None:
        super().__init__(parent)
        # Names already configured, listed by the caller in the background (no subprocess here)
        self._existing_names = list(existing_names or [])
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
        self.conditional_layout.addRow(tr("URL WebDAV:"), self.webdav_url_input)

        # S3 Fields
        self.s3_key_input = QLineEdit()
        self.s3_key_input.setPlaceholderText("Access Key ID")
        self.s3_secret_input = QLineEdit()
        self.s3_secret_input.setPlaceholderText("Secret Access Key")
        self.s3_secret_input.setEchoMode(QLineEdit.Password)
        self.s3_endpoint_input = QLineEdit()
        self.s3_endpoint_input.setPlaceholderText(tr("Vazio = Amazon S3; ou endpoint MinIO/Wasabi/etc."))
        self.conditional_layout.addRow(tr("S3 Access Key:"), self.s3_key_input)
        self.conditional_layout.addRow(tr("S3 Secret Key:"), self.s3_secret_input)
        self.conditional_layout.addRow(tr("S3 Endpoint:"), self.s3_endpoint_input)

        # Mega Fields
        self.mega_user_input = QLineEdit()
        self.mega_user_input.setPlaceholderText("email@example.com")
        self.mega_pass_input = QLineEdit()
        self.mega_pass_input.setPlaceholderText("Password")
        self.mega_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow(tr("Mega Email:"), self.mega_user_input)
        self.conditional_layout.addRow(tr("Mega Password:"), self.mega_pass_input)

        # FTP / SFTP Fields
        self.ftp_host_input = QLineEdit()
        self.ftp_host_input.setPlaceholderText(tr("ftp.example.com ou ip"))
        self.ftp_user_input = QLineEdit()
        self.ftp_user_input.setPlaceholderText("Username")
        self.ftp_pass_input = QLineEdit()
        self.ftp_pass_input.setPlaceholderText("Password")
        self.ftp_pass_input.setEchoMode(QLineEdit.Password)
        self.conditional_layout.addRow(tr("Host:"), self.ftp_host_input)
        self.conditional_layout.addRow(tr("User:"), self.ftp_user_input)
        self.conditional_layout.addRow(tr("Password:"), self.ftp_pass_input)

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
            self.conditional_layout.setRowVisible(self.ftp_user_input, True)
            self.conditional_layout.setRowVisible(self.ftp_pass_input, True)
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
            if not (self.webdav_url_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text()):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "s3":
            if not (self.s3_key_input.text().strip() and self.s3_secret_input.text()):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype == "mega":
            if not (self.mega_user_input.text().strip() and self.mega_pass_input.text()):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return
        elif rtype in ("ftp", "sftp"):
            if not (self.ftp_host_input.text().strip() and self.ftp_user_input.text().strip() and self.ftp_pass_input.text()):
                QMessageBox.warning(self, tr("Erro de Input"), tr("Por favor, preencha todos os campos obrigatórios do provedor selecionado."))
                return

        super().accept()
        
    def get_data(self) -> tuple[str, str, dict[str, str]]:
        rtype = self.type_combo.currentData()
        extra_params = {}
        if rtype == "webdav":
            url = self.webdav_url_input.text().strip()
            user = self.ftp_user_input.text().strip()
            password = self.ftp_pass_input.text()
            extra_params["vendor"] = "other"
            if url:
                extra_params["url"] = url
            if user:
                extra_params["user"] = user
            if password:
                extra_params["pass"] = password
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
        return self.name_input.text().strip().rstrip(":"), rtype, extra_params


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

        # Background runner for rclone subprocess calls (never block the GUI thread)
        self.runner = TaskRunner(self)
        self._remotes_cache: Optional[list[str]] = None
        self._upload_dialog: Optional[QProgressDialog] = None
        self._upload_timer: Optional[QTimer] = None
        self._upload_poll_busy = False

        # Initialize floating status popup panel
        self.status_popup = StatusPopup(daemon)
        
        self._menu = QMenu()
        
        # Bloco 1 (Topo): Status atual da nuvem (desabilitado)
        self.action_status = QAction(f"DucksDrive • {tr('Desconectado')}", self)
        self.action_status.setEnabled(False)
        self._menu.addAction(self.action_status)
        
        self._menu.addSeparator()
        
        # Bloco 2 (Ações): Produtividade
        self.action_open = QAction(tr("Abrir Pasta Local"), self)
        self.action_open.setEnabled(False)
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
        self.menu_remotes.aboutToShow.connect(self._on_remotes_menu_about_to_show)
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

    def refresh_remotes(self, callback: Optional[Callable[[list[str]], None]] = None) -> None:
        """
        Lists the configured remotes in a background thread, updates the cache/menu and then
        calls `callback(remotes)` in the GUI thread. On failure the previous list is kept.
        """
        def _done(result: object) -> None:
            names = result if isinstance(result, list) else (self._remotes_cache or [])
            changed = names != self._remotes_cache
            self._remotes_cache = names
            if changed:
                self._populate_remotes_menu()
            if callback is not None:
                callback(names)

        self.runner.submit(list_remote_names, callback=_done)

    def _on_remotes_menu_about_to_show(self) -> None:
        """Shows the cached list instantly and refreshes it in the background."""
        self._populate_remotes_menu()
        self.refresh_remotes()

    def shutdown_workers(self) -> None:
        """Waits for pending background rclone calls. Call only when the app is exiting."""
        self.runner.wait_all()

    def _unmount_current(self) -> None:
        """Menu handler: unmount without freezing the GUI."""
        self.daemon.stop_async()

    def _populate_remotes_menu(self) -> None:
        """Dynamically populates the 'Gerenciar Nuvens' submenu without leaking memory."""
        for act in self.menu_remotes.actions():
            if act.menu():
                act.menu().deleteLater()
            act.deleteLater()
        self.menu_remotes.clear()
        
        remotes = self._remotes_cache
        if remotes is None:
            loading_action = QAction(tr("Carregando nuvens..."), self.menu_remotes)
            loading_action.setEnabled(False)
            self.menu_remotes.addAction(loading_action)
            self.menu_remotes.addSeparator()
        elif remotes:
            for remote in remotes:
                is_active = self.daemon.is_running() and self.daemon.current_remote and self.daemon.current_remote.rstrip(":") == remote.rstrip(":")
                label = f"✓ {remote} (" + tr("Ativo") + ")" if is_active else remote
                remote_menu = QMenu(label, self.menu_remotes)
                if is_active:
                    action_unmount_local = QAction(tr("Desmontar"), remote_menu)
                    action_unmount_local.triggered.connect(self._unmount_current)
                    remote_menu.addAction(action_unmount_local)
                else:
                    action_mount = QAction(tr("Montar"), remote_menu)
                    action_mount.triggered.connect(lambda checked=False, r=remote: self.daemon.start(r, read_only=False))
                    remote_menu.addAction(action_mount)

                    action_mount_ro = QAction(tr("Montar (Somente Leitura)"), remote_menu)
                    action_mount_ro.triggered.connect(lambda checked=False, r=remote: self.daemon.start(r, read_only=True))
                    remote_menu.addAction(action_mount_ro)
                
                self.menu_remotes.addMenu(remote_menu)
            self.menu_remotes.addSeparator()
        else:
            empty_action = QAction(tr("Nenhuma nuvem configurada"), self.menu_remotes)
            empty_action.setEnabled(False)
            self.menu_remotes.addAction(empty_action)
            self.menu_remotes.addSeparator()

        self.action_unmount = QAction(tr("Desconectar Atual"), self.menu_remotes)
        self.action_unmount.triggered.connect(self._unmount_current)
        self.action_unmount.setEnabled(self.daemon.is_running())
        self.menu_remotes.addAction(self.action_unmount)

        self.action_new = QAction(tr("Adicionar Nova Nuvem..."), self.menu_remotes)
        self.action_new.triggered.connect(self._show_new_remote_dialog)
        self.menu_remotes.addAction(self.action_new)

        self.action_delete = QAction(tr("Remover Nuvem..."), self.menu_remotes)
        self.action_delete.triggered.connect(self._show_delete_selector)
        self.menu_remotes.addAction(self.action_delete)

    def retranslate_ui(self) -> None:
        """Updates all tray menu labels and the floating status popup in real time when language changes."""
        self.action_open.setText(tr("Abrir Pasta Local"))
        self.action_panel.setText(tr("Abrir Painel de Transferências"))
        self.action_connect.setText(tr("Selecionar Nuvem para Conectar..."))
        self.menu_remotes.setTitle(tr("Gerenciar Nuvens"))
        self.action_settings.setText(tr("Configurações..."))
        self.action_exit.setText(tr("Sair"))

        status_map = {
            "stopped": tr("Desconectado"),
            "starting": tr("Conectando..."),
            "mounted": tr("Conectado"),
            "unmounting": tr("Desconectando..."),
            "error": tr("Erro"),
        }
        state_label = status_map.get(self.daemon.state, self.daemon.state.capitalize())
        remote_name = self.daemon.current_remote or "DucksDrive"
        clean_name = remote_name.rstrip(":")
        status_header = f"{clean_name} • {state_label}"
        self.action_status.setText(status_header)
        self.setToolTip(status_header)
        self._populate_remotes_menu()
        self.status_popup.retranslate_ui()

    def _show_settings_dialog(self) -> None:
        """Opens the settings configuration dialog and applies language/bwlimit changes live."""
        dialog = SettingsDialog(parent=None, daemon=self.daemon)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        if dialog.exec() == QDialog.Accepted:
            if dialog.language_changed:
                self.retranslate_ui()
            if dialog.theme_changed:
                self.status_popup.apply_theme(str(dialog.config.get("theme") or "auto"))
            if (
                dialog.bwlimit_changed
                and self.daemon.is_running()
                and self.daemon.rc_credentials
                and not self.status_popup.is_paused
            ):
                new_rate = str(dialog.config.get("bwlimit") or "off")
                self.runner.submit(set_rc_bwlimit, self.daemon.rc_credentials, new_rate)

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
        self._populate_remotes_menu()
        
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
        QMessageBox.critical(None, tr("Erro de Montagem"), tr(error))

    def _set_status_and_tooltip(self, status_text: Optional[str] = None, tooltip_text: Optional[str] = None) -> None:
        """Updates menu status action text and tray tooltip only when changed to avoid redundant DBus IPC."""
        if status_text is not None and self.action_status.text() != status_text:
            self.action_status.setText(status_text)
        if tooltip_text is not None and self.toolTip() != tooltip_text:
            self.setToolTip(tooltip_text)

    @Slot(object)
    def update_stats(self, stats: StatsData) -> None:
        """Updates the tray tooltip, menu status, and floating status popup with live telemetry."""
        self.latest_stats = stats
        if stats.is_online:
            has_active = stats.has_active_transfers
            
            if has_active:
                self._was_syncing = True
                self.update_tray_icon("syncing")
                if stats.active_transfers:
                    t = stats.active_transfers[0]
                    name = t.get("name", tr("Arquivo"))
                    pct = int(t.get("percentage") or 0)
                    text = f"{tr('A enviar:')} {name} ({pct}%) • {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                    self._set_status_and_tooltip(text, f"{APP_NAME}: {text}")
                else:
                    text = f"{tr('Velocidade:')} {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                    self._set_status_and_tooltip(text, f"{APP_NAME}: {stats.status_text}")
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
                remote_name = self.daemon.current_remote or "DucksDrive"
                clean_name = remote_name.rstrip(":")
                text = f"{clean_name} • {tr('Conectado')} ({stats.speed_str})"
                self._set_status_and_tooltip(text, f"{APP_NAME}: {stats.status_text}")
        else:
            self._was_syncing = False
            self.update_tray_icon("error")
            if self.daemon.state == "mounted":
                self._set_status_and_tooltip(tr("Status: Conexão Perdida"), None)

        # Forward telemetry to floating status popup
        self.status_popup.update_stats(stats)

    def _has_active_transfer(self) -> bool:
        """Checks if there is any active file transfer in progress."""
        return bool(self.latest_stats and self.latest_stats.has_active_transfers)

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
        """
        Checks (off the GUI thread) whether files are still waiting in the VFS upload queue
        before exiting, so the user can wait for them instead of relying on the 10s kill timeout.
        """
        creds = self.daemon.rc_credentials if self.daemon.is_running() else None
        if creds is None:
            self.request_exit.emit()
            return
        self.runner.submit(fetch_vfs_queue, creds, callback=self._on_quit_queue_checked)

    def _on_quit_queue_checked(self, pending: Optional[int]) -> None:
        """GUI-thread continuation of _confirm_quit with the number of files still queued for upload."""
        pending_count = int(pending or 0)

        if pending_count > 0:
            box = QMessageBox(
                QMessageBox.Warning,
                tr("Transferências Ativas"),
                tr("%d arquivo(s) ainda estão sendo enviados para a nuvem.\nSe sair agora, o envio será retomado na próxima vez que esta nuvem for montada.") % pending_count,
                parent=None,
            )
            btn_wait = box.addButton(tr("Aguardar e sair"), QMessageBox.AcceptRole)
            btn_quit = box.addButton(tr("Sair assim mesmo"), QMessageBox.DestructiveRole)
            btn_cancel = box.addButton(tr("Cancelar"), QMessageBox.RejectRole)
            box.setDefaultButton(btn_wait)
            box.setEscapeButton(btn_cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_wait:
                self._wait_for_uploads_then_quit(pending_count)
            elif clicked is btn_quit:
                self.request_exit.emit()
            return

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

    def _wait_for_uploads_then_quit(self, pending: int) -> None:
        """Shows a cancellable progress dialog and exits once the VFS upload queue is empty."""
        if self._upload_dialog is not None:
            return
        dialog = QProgressDialog(
            tr("Aguardando o envio de %d arquivo(s) para a nuvem...") % pending,
            tr("Cancelar"), 0, 0, None
        )
        dialog.setWindowTitle(tr("Aguardando envios"))
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._cancel_upload_wait)
        self._upload_dialog = dialog

        timer = QTimer(self)
        timer.setInterval(1000)
        timer.timeout.connect(self._poll_upload_queue)
        self._upload_timer = timer

        dialog.show()
        timer.start()

    def _poll_upload_queue(self) -> None:
        creds = self.daemon.rc_credentials
        if creds is None:
            self._finish_upload_wait(proceed=True)  # mount is gone; nothing left to wait for
            return
        if self._upload_poll_busy:
            return
        self._upload_poll_busy = True
        self.runner.submit(fetch_vfs_queue, creds, callback=self._on_upload_poll_result)

    def _on_upload_poll_result(self, pending: Optional[int]) -> None:
        self._upload_poll_busy = False
        dialog = self._upload_dialog
        if dialog is None:
            return  # cancelled meanwhile
        if not pending:  # 0 = queue drained, None = RC unreachable (rclone gone)
            self._finish_upload_wait(proceed=True)
        else:
            dialog.setLabelText(tr("Aguardando o envio de %d arquivo(s) para a nuvem...") % pending)

    def _cancel_upload_wait(self) -> None:
        self._finish_upload_wait(proceed=False)

    def _finish_upload_wait(self, proceed: bool) -> None:
        dialog, timer = self._upload_dialog, self._upload_timer
        self._upload_dialog = None
        self._upload_timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if dialog is not None:
            try:
                dialog.canceled.disconnect(self._cancel_upload_wait)  # close() would emit canceled()
            except (RuntimeError, TypeError):
                pass
            dialog.close()
            dialog.deleteLater()
        if proceed:
            self.request_exit.emit()

    def _open_folder(self) -> None:
        """Opens the mount point in the system file manager using QDesktopServices."""
        path = self.daemon.mount_point
        if is_path_mounted(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception as e:
                logger.error("Failed to open folder with QDesktopServices: %s", e)
        else:
            QMessageBox.warning(None, tr("Pasta Ausente"), tr("O diretório %s não existe.") % path)

    def _show_delete_selector(self) -> None:
        """Lists remotes in the background, then shows a dialog to select and delete one."""
        self.refresh_remotes(self._on_delete_selector_remotes)

    def _on_delete_selector_remotes(self, remotes: list[str]) -> None:
        if not remotes:
            QMessageBox.information(None, tr("Nenhuma Nuvem"), tr("Não existem nuvens configuradas para remover."))
            return

        remote, ok = QInputDialog.getItem(
            None, tr("Remover Nuvem"), tr("Selecione a nuvem que deseja remover:"), remotes, 0, False
        )
        if not (ok and remote):
            return

        active_remote = self.daemon.current_remote
        creds = self.daemon.rc_credentials
        if active_remote and active_remote.rstrip(":") == remote.rstrip(":") and creds is not None:
            # Active remote: find out if uploads are still pending so the user is warned
            self.runner.submit(
                fetch_vfs_queue, creds,
                callback=lambda pending, r=remote: self._confirm_and_delete_remote(r, int(pending or 0)),
            )
        else:
            self._confirm_and_delete_remote(remote, 0)

    def _confirm_and_delete_remote(self, remote: str, pending: int) -> None:
        text = tr("Tem certeza que deseja excluir as configurações de '%s'?\nEsta ação não pode ser desfeita.") % remote
        if pending > 0:
            text += "\n\n" + tr("ATENÇÃO: %d arquivo(s) ainda aguardam envio para esta nuvem e não serão enviados.") % pending
        res = QMessageBox.warning(
            None,
            tr("Confirmar Exclusão"),
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res != QMessageBox.Yes:
            return

        # Security logic: if active, stop daemon first (in the background), then delete
        active_remote = self.daemon.current_remote
        if active_remote and active_remote.rstrip(":") == remote.rstrip(":"):
            logger.info("Active remote '%s' is being deleted. Stopping daemon...", remote)
            self.daemon.stop_async(on_done=lambda: self._delete_remote_in_background(remote))
        else:
            self._delete_remote_in_background(remote)

    def _delete_remote_in_background(self, remote: str) -> None:
        self.runner.submit(
            delete_remote, remote,
            callback=lambda deleted, r=remote: self._on_remote_deleted(r, bool(deleted)),
        )

    def _on_remote_deleted(self, remote: str, deleted: bool) -> None:
        self.refresh_remotes()
        if deleted:
            if str(load_config().get("last_remote") or "").rstrip(":") == remote.rstrip(":"):
                save_last_remote("")
            QMessageBox.information(None, tr("Nuvem Removida"), tr("A nuvem '%s' foi removida com sucesso.") % remote)
        else:
            QMessageBox.critical(None, tr("Erro"), tr("Falha ao remover a nuvem '%s'.") % remote)

    def _show_mount_selector(self) -> None:
        """Lists remotes in the background, then shows a dialog to select which one to mount."""
        self.refresh_remotes(self._on_mount_selector_remotes)

    def _on_mount_selector_remotes(self, remotes: list[str]) -> None:
        if not remotes:
            msg = tr("Nenhuma nuvem configurada.\nGostaria de configurar uma agora?")
            res = QMessageBox.question(None, tr("Nenhuma Nuvem"), msg, QMessageBox.Yes | QMessageBox.No)
            if res == QMessageBox.Yes:
                self._show_new_remote_dialog()
            return

        remote, ok = QInputDialog.getItem(
            None, tr("Conectar Nuvem"), tr("Selecione uma nuvem para conectar:"), remotes, 0, False
        )
        if not ok or not remote:
            return

        if self.daemon.is_running():
            active_remote = (self.daemon.current_remote or "").rstrip(":")
            if active_remote == remote.rstrip(":"):
                return
            res = QMessageBox.question(
                None,
                tr("Substituir Drive Ativo"),
                tr("Um drive já está conectado (%s). Deseja desconectá-lo e conectar ao novo remoto configurado?") % self.daemon.current_remote,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return

        self.daemon.start(remote)

    def _show_new_remote_dialog(self) -> None:
        """Handles the flow for creating a new remote via background thread."""
        self.refresh_remotes(self._open_new_remote_dialog)

    def _open_new_remote_dialog(self, existing_names: list[str]) -> None:
        dialog = NewRemoteDialog(parent=None, existing_names=existing_names)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.Accepted:
            name, rtype, extra_params = dialog.get_data()
            if not name:
                QMessageBox.warning(None, tr("Erro de Input"), tr("O nome da nuvem não pode estar vazio."))
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
                    # daemon.start() tears the old mount down in the background before mounting

                # Add a 1-second QTimer delay to ensure rclone config file flush completes before mounting
                QTimer.singleShot(1000, lambda: self.daemon.start(remote_name))
        else:
            self._pending_remote_name = None
            if "cancelled" not in message.lower():
                QMessageBox.critical(None, tr("Falha na Configuração"), message)
