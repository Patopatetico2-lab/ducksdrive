"""
gui_tray.py - PySide6 System Tray Interface and Dialogs for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Provides the primary user interface including the System Tray icon,
context menus, status notifications, and dialogs for remote management.
"""

import logging
import os
import re
import sys
import time
from typing import Any, Callable, Optional

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
    delete_remote,
    check_remote_connection,
    reconnect_remote,
)
from rclone_daemon import RcloneDaemon, is_path_mounted
from rclone_settings import apply_app_theme, load_config, save_last_remote
from rclone_stats import StatsData, fetch_vfs_queue, rc_vfs_refresh, set_rc_bwlimit
from status_popup import StatusPopup
from settings_dialog import SettingsDialog
from log_viewer import LogViewerDialog
from remote_settings_dialog import RemoteSettingsDialog
from notifications import notify_sync_complete, notify_mount_success, send_desktop_notification

logger = logging.getLogger(__name__)

APP_NAME = "DucksDrive"


def get_app_icon() -> QIcon:
    """
    Returns the application icon by loading the physical icon.svg file
    using an absolute path based on the script location.
    """
    current_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
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

        self.btn_create = QPushButton(tr("Iniciar Autorização no Browser"))
        self.btn_create.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 8px 14px; border-radius: 4px; font-weight: bold;"
        )
        self.btn_create.clicked.connect(self.accept)
        layout.addWidget(self.btn_create)

        self._on_provider_changed(0)
        self.apply_theme(str(load_config().get("theme") or "auto"))

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies the active theme palette to the NewRemoteDialog."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
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


class RcloneTrayIcon(QSystemTrayIcon):
    """Main System Tray application logic."""

    request_exit = Signal()

    def __init__(
        self,
        daemon: Optional[RcloneDaemon] = None,
        parent: Optional[QWidget] = None,
        tray_available: bool = True,
        daemons: Optional[dict[str, RcloneDaemon]] = None,
        controller: Optional[Any] = None,
    ) -> None:
        super().__init__(get_tray_icon("idle"), parent)
        self.tray_available = tray_available
        self.controller = controller
        self.daemons: dict[str, RcloneDaemon] = daemons.copy() if daemons else {}
        self._fallback_daemon: Optional[RcloneDaemon] = daemon
        if daemon is not None:
            clean = (daemon.current_remote or "default").rstrip(":")
            self.daemons[clean] = daemon

        self.setToolTip(f"{APP_NAME}: {tr('Desconectado')}")
        self._pending_remote_name: Optional[str] = None
        self._creation_thread: Optional[RemoteCreationThread] = None
        self._old_creation_threads: list[RemoteCreationThread] = []
        self._settings_dialog: Optional[SettingsDialog] = None
        self._new_remote_dialog: Optional[NewRemoteDialog] = None
        self._log_viewer_dialog: Optional[LogViewerDialog] = None
        self.latest_stats: Optional[StatsData] = None
        self._was_syncing = False
        self._idle_confirm_polls = 0
        self._current_icon_state = "idle"
        self._latest_release_url = "https://github.com/Patopatetico2-lab/ducksdrive/releases/latest"
        self._remote_syncing_state: dict[str, bool] = {}

        # Background runner for rclone subprocess calls (never block the GUI thread)
        self.runner = TaskRunner(self)
        self._remotes_cache: Optional[list[str]] = None
        self._upload_dialog: Optional[QProgressDialog] = None
        self._upload_timer: Optional[QTimer] = None
        self._upload_poll_busy = False

        # Initialize floating status popup panel (or standalone dashboard window)
        self.status_popup = StatusPopup(
            daemon=daemon,
            daemons=self.daemons,
            window_mode=not tray_available,
        )
        self.status_popup._tray = self

        self._menu = QMenu()

        # Bloco 1 (Topo): Status atual da nuvem (desabilitado)
        self.action_status = QAction(f"DucksDrive • {tr('Desconectado')}", self)
        self.action_status.setEnabled(False)
        self._menu.addAction(self.action_status)

        # Update notification action (shown only when a newer release is detected)
        self.action_update = QAction(tr("★ Nova versão disponível!"), self)
        self.action_update.setVisible(False)
        self.action_update.triggered.connect(self._open_update_url)
        self._menu.addAction(self.action_update)

        self._menu.addSeparator()

        # Bloco 2 (Ações): Produtividade
        self.action_open = QAction(tr("Abrir Pasta Local"), self)
        self.action_open.setEnabled(False)
        self.action_open.triggered.connect(self._open_folder)
        self._menu.addAction(self.action_open)

        self.action_refresh_vfs = QAction(tr("Atualizar Pastas"), self)
        self.action_refresh_vfs.setEnabled(False)
        self.action_refresh_vfs.triggered.connect(self._refresh_vfs)
        self._menu.addAction(self.action_refresh_vfs)

        self.action_panel = QAction(tr("Abrir Painel de Transferências"), self)
        self.action_panel.triggered.connect(self._show_transfer_panel)
        self._menu.addAction(self.action_panel)

        self._menu.addSeparator()

        # Bloco 3 (Gerenciamento): Submenu dinâmico de Nuvens
        self.menu_remotes = self._menu.addMenu(tr("Gerenciar Nuvens"))
        self.menu_remotes.aboutToShow.connect(self._on_remotes_menu_about_to_show)
        self._populate_remotes_menu()

        self.action_logs = QAction(tr("Ver Logs de Diagnóstico..."), self)
        self.action_logs.triggered.connect(self._show_log_viewer_dialog)
        self._menu.addAction(self.action_logs)

        self.action_settings = QAction(tr("Configurações..."), self)
        self.action_settings.triggered.connect(self._show_settings_dialog)
        self._menu.addAction(self.action_settings)

        self._menu.addSeparator()

        # Bloco 4 (Fundo): Sair
        self.action_exit = QAction(tr("Sair"), self)
        self.action_exit.triggered.connect(self._confirm_quit)
        self._menu.addAction(self.action_exit)

        self.setContextMenu(self._menu)
        self.status_popup.set_tray_menu(self._menu)
        self.apply_theme(str(load_config().get("theme") or "auto"))

        if daemon is not None:
            daemon.state_changed.connect(lambda s: self._on_state_changed(s, getattr(daemon, "current_remote", None)))
            daemon.mount_error.connect(self._on_mount_error)
        for r, d in list(self.daemons.items()):
            d.state_changed.connect(lambda s, rem=r: self._on_state_changed(s, rem))
            d.mount_error.connect(self._on_mount_error)

        self.activated.connect(self._on_activated)

    @property
    def daemon(self) -> RcloneDaemon:
        active = self.get_active_daemon()
        if active is not None:
            return active
        if self._fallback_daemon is not None:
            return self._fallback_daemon
        self._fallback_daemon = RcloneDaemon(parent=self)
        return self._fallback_daemon

    @daemon.setter
    def daemon(self, value: Optional[RcloneDaemon]) -> None:
        self._fallback_daemon = value
        if value is not None:
            clean = (value.current_remote or "default").rstrip(":")
            self.daemons[clean] = value

    def get_active_daemon(self) -> Optional[RcloneDaemon]:
        for d in self.daemons.values():
            if d.is_running():
                return d
        if self.daemons:
            return next(iter(self.daemons.values()))
        return self._fallback_daemon

    def active_daemons(self) -> dict[str, RcloneDaemon]:
        return {r: d for r, d in self.daemons.items() if d.is_running()}

    def get_or_create_daemon(self, remote_name: str) -> RcloneDaemon:
        clean = remote_name.rstrip(":")
        if clean in self.daemons:
            return self.daemons[clean]
        d = RcloneDaemon(parent=self, remote_name=clean)
        self.register_daemon(clean, d)
        return d

    def register_daemon(self, remote_name: str, daemon: RcloneDaemon) -> None:
        clean = remote_name.rstrip(":")
        self.daemons[clean] = daemon
        self.status_popup.register_daemon(clean, daemon)
        daemon.state_changed.connect(lambda s, rem=clean: self._on_state_changed(s, rem))
        daemon.mount_error.connect(self._on_mount_error)

    def _open_update_url(self) -> None:
        """Opens GitHub releases page in default web browser."""
        QDesktopServices.openUrl(QUrl(self._latest_release_url))

    def notify_update_available(self, latest_ver: str, release_url: str) -> None:
        """Shows update action in menu and displays a tray message if available."""
        self._latest_release_url = release_url
        self.action_update.setText(tr("★ Nova versão %s disponível!") % latest_ver)
        self.action_update.setVisible(True)
        if self.tray_available:
            self.showMessage(
                APP_NAME,
                tr("Nova versão do DucksDrive disponível (%s)!") % latest_ver,
                QSystemTrayIcon.Information,
                5000,
            )

    def show_window_mode(self) -> None:
        """Shows the dashboard window for environments without a system tray."""
        self.status_popup.show_window_mode()

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies the active theme globally to QApplication, the tray menus, and the status popup."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self._menu.setPalette(palette)
            self.menu_remotes.setPalette(palette)
        self.status_popup.apply_theme(active_pref)

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
        """Waits for pending background rclone calls and creation threads. Call only when the app is exiting."""
        self.runner.wait_all()
        if hasattr(self, "_creation_thread") and self._creation_thread is not None:
            self._creation_thread.cancel()
            if self._creation_thread.isRunning():
                self._creation_thread.wait(2000)
        for t in list(getattr(self, "_old_creation_threads", [])):
            try:
                if t.isRunning():
                    t.wait(2000)
            except RuntimeError:
                pass
        self.status_popup.shutdown_workers()

    def _safe_unmount_or_switch(self, on_confirmed: Callable[[], None]) -> None:
        """
        Verifies if there are files waiting in the VFS queue or actively transferring
        before stopping or switching remotes. If files are pending, asks confirmation.
        """
        if not self.daemon.is_running():
            on_confirmed()
            return

        creds = self.daemon.rc_credentials
        if creds is None:
            on_confirmed()
            return

        def _on_queue_checked(pending: Optional[int]) -> None:
            pending_count = int(pending or 0)
            has_active = self._has_active_transfer()
            if pending_count > 0 or has_active:
                if pending_count > 0:
                    msg = (
                        tr("Ainda existem %d arquivo(s) sendo sincronizados com a nuvem.\n"
                           "Desconectar agora pode causar perda de dados ou envios incompletos.\n\n"
                           "Tem certeza de que deseja desconectar?")
                        % pending_count
                    )
                else:
                    msg = tr(
                        "Transferências de arquivos estão em andamento.\n"
                        "Desconectar agora pode causar perda de dados ou envios incompletos.\n\n"
                        "Tem certeza de que deseja desconectar?"
                    )
                res = QMessageBox.warning(
                    None,
                    tr("Transferências Pendentes"),
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if res != QMessageBox.StandardButton.Yes:
                    return
            on_confirmed()

        self.runner.submit(fetch_vfs_queue, creds, callback=_on_queue_checked)

    def _mount_remote(self, remote_name: str, read_only: bool = False) -> None:
        """Starts mounting the specified remote in background without disconnecting others."""
        clean = remote_name.rstrip(":")
        if self.controller is not None and hasattr(self.controller, "mount_remote"):
            self.controller.mount_remote(clean, read_only=read_only)
        else:
            d = self.get_or_create_daemon(clean)
            d.start(clean, read_only=read_only)

    def _unmount_remote(self, remote_name: str) -> None:
        """Unmounts the specified remote safely checking for active uploads."""
        clean = remote_name.rstrip(":")
        d = self.daemons.get(clean)
        if not d or not d.is_running():
            return

        def _do_stop() -> None:
            if self.controller is not None and hasattr(self.controller, "unmount_remote"):
                self.controller.unmount_remote(clean)
            else:
                d.stop_async()

        self._safe_unmount_daemon(d, _do_stop)

    def _unmount_all(self) -> None:
        """Unmounts all running cloud drives after checking pending transfers."""
        active = list(self.active_daemons().values())
        if not active:
            return

        def _do_stop_all() -> None:
            if self.controller is not None and hasattr(self.controller, "unmount_all"):
                self.controller.unmount_all()
            else:
                for d in active:
                    d.stop_async()

        self._safe_unmount_daemon(active[0], _do_stop_all)

    def _safe_unmount_daemon(self, daemon: RcloneDaemon, on_confirmed: Callable[[], None]) -> None:
        """Checks pending uploads on a specific daemon before unmounting."""
        if not daemon.is_running() or not daemon.rc_credentials:
            on_confirmed()
            return

        creds = daemon.rc_credentials

        def _on_queue_checked(pending: Optional[int]) -> None:
            pending_count = int(pending or 0)
            if pending_count > 0:
                msg = (
                    tr("Ainda existem %d arquivo(s) sendo sincronizados com a nuvem.\n"
                       "Desconectar agora pode causar perda de dados ou envios incompletos.\n\n"
                       "Tem certeza de que deseja desconectar?")
                    % pending_count
                )
                res = QMessageBox.warning(
                    None,
                    tr("Transferências Pendentes"),
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if res != QMessageBox.StandardButton.Yes:
                    return
            on_confirmed()

        self.runner.submit(fetch_vfs_queue, creds, callback=_on_queue_checked)

    def _show_remote_settings_dialog(self, remote_name: str) -> None:
        """Opens per-remote configuration dialog."""
        dialog = RemoteSettingsDialog(remote_name, parent=None)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            clean_r = remote_name.rstrip(":")
            d = self.daemons.get(clean_r)
            if d is not None and d.is_running():
                res = QMessageBox.question(
                    None,
                    tr("Remontar Nuvem"),
                    tr("Deseja remontar o drive '%s' agora para aplicar as novas configurações?") % clean_r,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if res == QMessageBox.StandardButton.Yes:
                    self._mount_remote(clean_r)

    def _show_log_viewer_dialog(self) -> None:
        """Opens the diagnostic log viewer dialog."""
        if self._log_viewer_dialog is not None and self._log_viewer_dialog.isVisible():
            self._log_viewer_dialog.raise_()
            self._log_viewer_dialog.activateWindow()
            return
        dialog = LogViewerDialog(parent=None)
        self._log_viewer_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _populate_remotes_menu(self) -> None:
        """Dynamically populates the 'Gerenciar Nuvens' submenu with multi-mount actions."""
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
                clean_r = remote.rstrip(":")
                d = self.daemons.get(clean_r)
                is_active = d is not None and d.is_running()
                label = f"✓ {remote} (" + tr("Ativo") + ")" if is_active else remote
                remote_menu = QMenu(label, self.menu_remotes)
                if is_active and d is not None:
                    action_open_r = QAction(tr("Abrir Pasta Local"), remote_menu)
                    action_open_r.triggered.connect(
                        lambda checked=False, mp=d.mount_point: QDesktopServices.openUrl(QUrl.fromLocalFile(mp))
                    )
                    remote_menu.addAction(action_open_r)

                    action_disconnect = QAction(tr("Desconectar"), remote_menu)
                    action_disconnect.triggered.connect(
                        lambda checked=False, r=remote: self._unmount_remote(r)
                    )
                    remote_menu.addAction(action_disconnect)

                    action_refresh_r = QAction(tr("Atualizar Pastas"), remote_menu)
                    action_refresh_r.triggered.connect(
                        lambda checked=False, daemon_item=d: daemon_item.refresh_vfs()
                    )
                    remote_menu.addAction(action_refresh_r)
                else:
                    action_mount = QAction(tr("Montar"), remote_menu)
                    action_mount.triggered.connect(
                        lambda checked=False, r=remote: self._mount_remote(r, read_only=False)
                    )
                    remote_menu.addAction(action_mount)

                    action_mount_ro = QAction(tr("Montar (Somente Leitura)"), remote_menu)
                    action_mount_ro.triggered.connect(
                        lambda checked=False, r=remote: self._mount_remote(r, read_only=True)
                    )
                    remote_menu.addAction(action_mount_ro)

                remote_menu.addSeparator()
                action_cfg = QAction(tr("Configurações da Nuvem..."), remote_menu)
                action_cfg.triggered.connect(
                    lambda checked=False, r=remote: self._show_remote_settings_dialog(r)
                )
                remote_menu.addAction(action_cfg)

                action_test = QAction(tr("Testar Conexão"), remote_menu)
                action_test.triggered.connect(lambda checked=False, r=remote: self._test_connection(r))
                remote_menu.addAction(action_test)

                action_delete_remote = QAction(tr("Remover"), remote_menu)
                action_delete_remote.triggered.connect(lambda checked=False, r=remote: self._request_delete_remote(r))
                remote_menu.addAction(action_delete_remote)

                self.menu_remotes.addMenu(remote_menu)

            active_count = len(self.active_daemons())
            if active_count > 1:
                self.menu_remotes.addSeparator()
                action_unmount_all = QAction(tr("Desconectar Todas as Nuvens"), self.menu_remotes)
                action_unmount_all.triggered.connect(self._unmount_all)
                self.menu_remotes.addAction(action_unmount_all)

            self.menu_remotes.addSeparator()
        else:
            empty_action = QAction(tr("Nenhuma nuvem configurada"), self.menu_remotes)
            empty_action.setEnabled(False)
            self.menu_remotes.addAction(empty_action)
            self.menu_remotes.addSeparator()

        self.action_new = QAction(tr("Adicionar Nova Nuvem..."), self.menu_remotes)
        self.action_new.triggered.connect(self._show_new_remote_dialog)
        self.menu_remotes.addAction(self.action_new)

    def retranslate_ui(self) -> None:
        """Updates all tray menu labels and the floating status popup in real time when language changes."""
        self.action_open.setText(tr("Abrir Pasta Local"))
        self.action_refresh_vfs.setText(tr("Atualizar Pastas"))
        self.action_panel.setText(tr("Abrir Painel de Transferências"))
        self.menu_remotes.setTitle(tr("Gerenciar Nuvens"))
        self.action_logs.setText(tr("Ver Logs de Diagnóstico..."))
        self.action_settings.setText(tr("Configurações..."))
        self.action_exit.setText(tr("Sair"))

        active = self.active_daemons()
        if len(active) > 1:
            names = ", ".join(active.keys())
            status_header = f"DucksDrive • {tr('%d Nuvens Conectadas') % len(active)} ({names})"
        elif len(active) == 1:
            r_name = next(iter(active.keys()))
            status_header = f"{r_name} • {tr('Conectado')}"
        else:
            status_header = f"DucksDrive • {tr('Desconectado')}"

        self.action_status.setText(status_header)
        self.setToolTip(status_header)
        self._populate_remotes_menu()
        self.status_popup.retranslate_ui()

    def _show_settings_dialog(self) -> None:
        """Opens the settings configuration dialog and applies language/bwlimit changes live."""
        if hasattr(self, "_settings_dialog") and self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(parent=None, daemon=self.daemon, runner=self.runner)
        self._settings_dialog = dialog
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        if dialog.exec() == QDialog.Accepted:
            self.apply_theme(str(dialog.config.get("theme") or "auto"))
            if dialog.language_changed:
                self.retranslate_ui()
            if (
                dialog.bwlimit_changed
                and self.daemon.is_running()
                and self.daemon.rc_credentials
                and not self.status_popup.is_paused
            ):
                new_rate = str(dialog.config.get("bwlimit") or "off")
                for d in self.active_daemons().values():
                    if d.rc_credentials:
                        self.runner.submit(set_rc_bwlimit, d.rc_credentials, new_rate)

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
    def _on_state_changed(self, state: str, remote_name: Optional[str] = None) -> None:
        """Updates UI elements based on daemon state across multi-mounts."""
        clean_remote = remote_name.rstrip(":") if remote_name else (self.daemon.current_remote or "")
        active = self.active_daemons()
        has_any_mounted = len(active) > 0

        self.action_open.setEnabled(has_any_mounted)
        self.action_refresh_vfs.setEnabled(has_any_mounted)

        status_map = {
            "stopped": tr("Desconectado"),
            "starting": tr("Conectando..."),
            "mounted": tr("Conectado"),
            "unmounting": tr("Desconectando..."),
            "error": tr("Erro"),
        }
        state_label = status_map.get(state, state.capitalize())

        if len(active) > 1:
            names = ", ".join(active.keys())
            status_header = f"DucksDrive • {tr('%d Nuvens Conectadas') % len(active)} ({names})"
        elif len(active) == 1:
            r_name = next(iter(active.keys()))
            status_header = f"{r_name} • {tr('Conectado')}"
        elif clean_remote:
            status_header = f"{clean_remote} • {state_label}"
        else:
            status_header = f"DucksDrive • {tr('Desconectado')}"

        self.action_status.setText(status_header)
        self.setToolTip(status_header)
        self._populate_remotes_menu()
        self.status_popup.update_mounted_remotes()

        if state == "mounted":
            self.update_tray_icon("idle")
            d = self.daemons.get(clean_remote)
            mp = d.mount_point if d else os.path.expanduser(f"~/CloudDrives/{clean_remote}")
            notify_mount_success(clean_remote, mp, fallback_tray=self if self.tray_available else None)
        elif state == "error":
            self.update_tray_icon("error")
        elif not any(d.state == "error" for d in self.daemons.values()):
            if not any(self._remote_syncing_state.values()):
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

    def update_stats_for_remote(self, remote_name: str, stats: StatsData) -> None:
        """Updates live telemetry for a specific remote across tray and status popup."""
        clean = remote_name.rstrip(":")
        self.latest_stats = stats
        self.status_popup.update_stats_for_remote(clean, stats)

        if stats.is_online:
            has_active = stats.has_active_transfers
            was_syncing = self._remote_syncing_state.get(clean, False)

            if has_active:
                self._remote_syncing_state[clean] = True
                self.update_tray_icon("syncing")
                menu_header = f"{clean} • {tr('Sincronizando...')}"
                if stats.active_transfers:
                    t = stats.active_transfers[0]
                    name = t.get("name", tr("Arquivo"))
                    pct = max(0, min(100, int(t.get("percentage") or 0)))
                    tooltip_detail = f"{tr('Transferindo:')} {name} ({pct}%) • {stats.speed_str} • {tr('ETA:')} {stats.eta_str}"
                    self._set_status_and_tooltip(menu_header, f"{APP_NAME}: {tooltip_detail}")
                else:
                    self._set_status_and_tooltip(menu_header, f"{APP_NAME}: {stats.status_text}")
            else:
                if was_syncing:
                    self._remote_syncing_state[clean] = False
                    if not any(self._remote_syncing_state.values()):
                        self.update_tray_icon("idle")
                    notify_sync_complete(
                        clean,
                        file_count=getattr(stats, "total_transfers", 0),
                        fallback_tray=self if self.tray_available else None,
                    )
                active = self.active_daemons()
                if len(active) <= 1:
                    menu_header = f"{clean} • {tr('Conectado')}"
                    self._set_status_and_tooltip(menu_header, f"{APP_NAME}: {stats.status_text}")
        else:
            self._remote_syncing_state[clean] = False
            if not any(self._remote_syncing_state.values()):
                self.update_tray_icon("idle")

    @Slot(object)
    def update_stats(self, stats: StatsData) -> None:
        """Backward-compatible update_stats."""
        clean = (self.daemon.current_remote or "default").rstrip(":")
        self.update_stats_for_remote(clean, stats)

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
        Checks (off the GUI thread) whether files are still waiting in any VFS upload queue
        before exiting, so the user can wait for them instead of relying on the 15s kill timeout.
        """
        if getattr(self, "_upload_dialog", None) is not None or getattr(self, "_quit_dialog_open", False):
            return
        active_creds = [d.rc_credentials for d in self.daemons.values() if d.is_running() and d.rc_credentials]
        if not active_creds:
            self.request_exit.emit()
            return

        self._quit_dialog_open = True

        def _check_all() -> int:
            total = 0
            for creds in active_creds:
                try:
                    p = fetch_vfs_queue(creds)
                    if p:
                        total += int(p)
                except Exception:
                    pass
            return total

        self.runner.submit(_check_all, callback=self._on_quit_queue_checked)

    def _on_quit_queue_checked(self, pending: Optional[int]) -> None:
        """GUI-thread continuation of _confirm_quit with the number of files still queued for upload."""
        self._quit_dialog_open = False
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
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if res != QMessageBox.StandardButton.Yes:
                return
        self.request_exit.emit()

    def _wait_for_uploads_then_quit(self, pending: int) -> None:
        """Shows a cancellable progress dialog and exits once all VFS upload queues are empty."""
        if self._upload_dialog is not None:
            return
        orig_rate = str(load_config().get("bwlimit") or "off")
        for d in self.daemons.values():
            if d.is_running() and d.rc_credentials is not None:
                d.is_user_paused = False
                self.runner.submit(set_rc_bwlimit, d.rc_credentials, orig_rate)

        self.status_popup.is_paused = False
        self.status_popup._apply_pause_button_ui()

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
        active_creds = [d.rc_credentials for d in self.daemons.values() if d.is_running() and d.rc_credentials]
        if not active_creds:
            self._finish_upload_wait(proceed=True)  # mounts are gone; nothing left to wait for
            return
        if self._upload_poll_busy:
            return
        self._upload_poll_busy = True

        def _check_all() -> int:
            total = 0
            for creds in active_creds:
                try:
                    p = fetch_vfs_queue(creds)
                    if p:
                        total += int(p)
                except Exception:
                    pass
            return total

        self.runner.submit(_check_all, callback=self._on_upload_poll_result)

    def _on_upload_poll_result(self, pending: Optional[int]) -> None:
        self._upload_poll_busy = False
        dialog = self._upload_dialog
        if dialog is None:
            return  # cancelled meanwhile
        if pending is None or pending <= 0:
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
        active = self.active_daemons()
        if len(active) == 1:
            d = next(iter(active.values()))
            path = d.mount_point
        elif len(active) > 1:
            path = os.path.abspath(os.path.expanduser("~/CloudDrives"))
        else:
            path = self.daemon.mount_point

        if os.path.exists(path) or is_path_mounted(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception as e:
                logger.error("Failed to open folder with QDesktopServices: %s", e)
        else:
            QMessageBox.warning(None, tr("Pasta Ausente"), tr("O diretório %s não existe.") % path)

    def _refresh_vfs(self) -> None:
        """Sends POST /vfs/refresh for all active daemons."""
        active = self.active_daemons()
        if not active:
            return
        for r, d in active.items():
            if d.rc_credentials:
                self.runner.submit(rc_vfs_refresh, d.rc_credentials, timeout=15.0)
        send_desktop_notification(
            APP_NAME,
            tr("Atualizando cache de pastas do rclone em segundo plano..."),
            fallback_tray=self if self.tray_available else None,
        )

    def _test_connection(self, remote: str) -> None:
        """Executes non-blocking rclone about diagnostic in background."""
        clean_remote = remote.rstrip(":")
        self.showMessage(APP_NAME, tr("Testando conexão com '%s'...") % clean_remote, QSystemTrayIcon.Information, 2000)
        self.runner.submit(
            check_remote_connection,
            clean_remote,
            callback=lambda res, r=clean_remote: self._on_connection_tested(r, res),
        )

    def _on_connection_tested(self, remote: str, result: Any) -> None:
        """GUI-thread callback for connection test results."""
        if not isinstance(result, tuple) or len(result) != 3:
            return
        is_healthy, message, is_oauth_expired = result
        if is_healthy:
            QMessageBox.information(None, tr("Teste de Conexão"), message)
        elif is_oauth_expired:
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                tr("Token OAuth Expirado"),
                message + "\n\n" + tr("Deseja reautorizar o acesso à sua conta agora através do navegador?"),
                parent=None,
            )
            btn_reauth = box.addButton(tr("Reautorizar no Navegador"), QMessageBox.ButtonRole.AcceptRole)
            btn_cancel = box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_reauth)
            box.setEscapeButton(btn_cancel)
            box.exec()
            if box.clickedButton() is btn_reauth:
                self._reauthorize_oauth(remote)
        else:
            QMessageBox.warning(None, tr("Falha na Conexão"), message)

    def _reauthorize_oauth(self, remote: str) -> None:
        """Handles non-blocking OAuth reauthorization in background, unmounting first if active."""
        clean_remote = remote.rstrip(":")
        d = self.daemons.get(clean_remote)
        was_mounted = bool(d and d.is_running())
        if was_mounted and d:
            d.stop()

        self.showMessage(
            APP_NAME,
            tr("Abrindo o navegador para reautorizar '%s'...") % clean_remote,
            QSystemTrayIcon.Information,
            5000,
        )
        self.runner.submit(
            reconnect_remote,
            clean_remote,
            callback=lambda res, r=clean_remote, wm=was_mounted: self._on_reconnect_done(r, wm, res),
        )

    def _on_reconnect_done(self, remote: str, was_mounted: bool, result: Any) -> None:
        """GUI-thread handler after rclone config reconnect finishes."""
        if not isinstance(result, tuple):
            return
        ret_code, output = result
        if ret_code == 0:
            QMessageBox.information(
                None,
                tr("Reautorização Concluída"),
                tr("A nuvem '%s' foi reautorizada com sucesso!") % remote,
            )
            if was_mounted:
                self._mount_remote(remote)
        else:
            clean_out = output.strip() if output else tr("Código de erro %d") % ret_code
            QMessageBox.warning(
                None,
                tr("Falha na Reautorização"),
                tr("Não foi possível reautorizar '%s':\n%s") % (remote, clean_out),
            )

    def _request_delete_remote(self, remote: str) -> None:
        """Initiates deletion of the selected remote, checking pending uploads first if active."""
        clean_remote = remote.rstrip(":")
        d = self.daemons.get(clean_remote)
        if d and d.is_running() and d.rc_credentials is not None:
            self.runner.submit(
                fetch_vfs_queue, d.rc_credentials,
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

        clean_remote = remote.rstrip(":")
        d = self.daemons.get(clean_remote)
        if d and d.is_running():
            logger.info("Active remote '%s' is being deleted. Stopping daemon...", remote)
            d.stop_async(on_done=lambda: self._delete_remote_in_background(remote))
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

    def _show_new_remote_dialog(self) -> None:
        """Handles the flow for creating a new remote via background thread."""
        if hasattr(self, "_new_remote_dialog") and self._new_remote_dialog is not None and self._new_remote_dialog.isVisible():
            self._new_remote_dialog.raise_()
            self._new_remote_dialog.activateWindow()
            return
        if hasattr(self, "_progress_box") and self._progress_box is not None and self._progress_box.isVisible():
            self._progress_box.raise_()
            self._progress_box.activateWindow()
            return
        self.refresh_remotes(self._open_new_remote_dialog)

    def _open_new_remote_dialog(self, existing_names: list[str]) -> None:
        dialog = NewRemoteDialog(parent=None, existing_names=existing_names)
        self._new_remote_dialog = dialog
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() == QDialog.Accepted:
            name, rtype, extra_params = dialog.get_data()
            if not name:
                QMessageBox.warning(None, tr("Erro de Input"), tr("O nome da nuvem não pode estar vazio."))
                return

            self._pending_remote_name = name
            if hasattr(self, "_creation_thread") and self._creation_thread is not None:
                old_t = self._creation_thread
                if old_t.isRunning():
                    if not hasattr(self, "_old_creation_threads"):
                        self._old_creation_threads = []
                    self._old_creation_threads.append(old_t)
                    old_t.finished.connect(lambda t=old_t: self._cleanup_old_creation_thread(t))
            self._creation_thread = RemoteCreationThread(name, rtype, extra_params=extra_params)

            is_oauth = rtype in ("drive", "onedrive", "dropbox", "box", "pcloud")
            msg = (tr("Por favor, complete a autorização no seu navegador para '%s'...") % name) if is_oauth else (tr("Configurando nuvem '%s'...") % name)

            # Show a progress message since this can take time
            progress = QMessageBox(QMessageBox.Information, tr("Configurando"), msg, QMessageBox.Cancel, parent=None)
            self._progress_box = progress
            # Cancel button and the window "X" both end up in rejected()
            progress.rejected.connect(self._creation_thread.cancel)

            self._creation_thread.output_line.connect(self._on_creation_output_line)
            self._creation_thread.finished_creation.connect(self._close_progress)
            self._creation_thread.finished_creation.connect(self._on_creation_finished)

            self._creation_thread.start()
            progress.exec()

    def _cleanup_old_creation_thread(self, thread: RemoteCreationThread) -> None:
        if hasattr(self, "_old_creation_threads") and thread in self._old_creation_threads:
            self._old_creation_threads.remove(thread)
        thread.deleteLater()

    @Slot(str)
    def _on_creation_output_line(self, line: str) -> None:
        """Displays any OAuth URL emitted by rclone inside the progress dialog so the user can copy it if needed."""
        progress = getattr(self, "_progress_box", None)
        if progress is not None and ("http://" in line or "https://" in line):
            progress.setInformativeText(line)

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
            self.refresh_remotes()
            logger.info("Remote successfully configured. Starting silent mount for '%s'", self._pending_remote_name)
            if self._pending_remote_name:
                remote_name = self._pending_remote_name
                self._pending_remote_name = None

                # Mount the newly configured cloud drive in background without disconnecting others
                QTimer.singleShot(1000, lambda: self._mount_remote(remote_name))
        else:
            self._pending_remote_name = None
            if not getattr(self._creation_thread, "is_cancelled", False):
                QMessageBox.critical(None, tr("Falha na Configuração"), message)
