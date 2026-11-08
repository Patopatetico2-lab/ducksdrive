"""
main.py - Entrypoint for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Orchestrates the application lifecycle:
1. Enforces single instance execution via QLockFile.
2. Configures dual logging (StreamHandler + RotatingFileHandler).
3. Validates system tray availability and rclone version (>= 1.63.0).
4. Initializes the Daemon, Stats Poller, and Tray UI.
5. Manages OS signals (SIGINT/SIGTERM) for graceful FUSE unmount.
"""

import logging
import os
import re
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Slot, QTimer, QLockFile
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QSystemTrayIcon

from rclone_config import TaskRunner, get_clean_env
from rclone_daemon import RcloneDaemon
from rclone_settings import (
    CONFIG_DIR,
    _sync_autostart,
    apply_app_theme,
    load_config,
    save_config,
    save_last_remote,
    set_system_default_palette,
)
from i18n import set_language, tr
from rclone_stats import RcloneStatsPoller
from filemanager_integration import integrate_mount, clean_integration
from gui_tray import RcloneTrayIcon, get_app_icon
from notifications import notify_mount_success, notify_connection_lost
from updater import UpdateCheckWorker

# Configure logging (Stream + RotatingFileHandler in ~/.local/share/ducksdrive/ducksdrive.log)
xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
LOG_DIR = os.path.join(xdg_data, "ducksdrive")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "ducksdrive.log")

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), file_handler]
)
logger = logging.getLogger("ducksdrive")

REQUIRED_RCLONE_VERSION = (1, 63, 0)


def get_rclone_version() -> Optional[tuple[int, int, int]]:
    """
    Executes 'rclone version' and parses the version tuple,
    ignoring pre-release or development suffixes (e.g. 1.73.5-beta, 1.74.0-DEV).
    """
    try:
        result = subprocess.run(
            ["rclone", "version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            env=get_clean_env()
        )
        match = re.search(r"rclone v?(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9.-]+)?", result.stdout)
        if match:
            return tuple(map(int, match.groups()))
    except Exception as e:
        logger.error("Could not determine rclone version: %s", e)
    return None


class RcloneAppController(QObject):
    """
    Controller to link background multi-cloud logic (Daemons/Stats) with the UI (Tray or Window Mode).
    """

    def __init__(self, app: QApplication, tray_available: bool = True) -> None:
        super().__init__()
        self.app = app
        self.tray_available = tray_available
        self.daemons: Dict[str, RcloneDaemon] = {}
        self.stats_pollers: Dict[str, RcloneStatsPoller] = {}
        self._old_pollers: List[RcloneStatsPoller] = []
        self._update_worker: Optional[UpdateCheckWorker] = None

        # Serial background runner for file-manager sidebar I/O
        self.runner = TaskRunner(self)
        self._is_shutting_down = False
        self._threads_cleaned = False

        # Initialize Tray UI (with Window Mode fallback if system tray is missing)
        self.tray = RcloneTrayIcon(
            daemon=None,
            tray_available=tray_available,
            daemons=self.daemons,
            controller=self,
        )

        # Connect signals
        self.tray.request_exit.connect(self.shutdown)
        self.app.aboutToQuit.connect(self._cleanup_threads)

        # Setup OS signal handling for clean FUSE unmount
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Periodically check for OS signals in the Qt loop
        self._signal_timer = QTimer()
        self._signal_timer.timeout.connect(lambda: None)  # Dummy to allow signal processing
        self._signal_timer.start(500)

    @property
    def daemon(self) -> RcloneDaemon:
        """Returns the primary or first active daemon for backward compatibility."""
        if self.daemons:
            for d in self.daemons.values():
                if d.is_running():
                    return d
            return next(iter(self.daemons.values()))
        return self.get_or_create_daemon("default")

    @property
    def stats_poller(self) -> Optional[RcloneStatsPoller]:
        """Returns the stats poller of the first active remote."""
        if self.stats_pollers:
            return next(iter(self.stats_pollers.values()))
        return None

    def get_or_create_daemon(self, remote_name: str) -> RcloneDaemon:
        """Retrieves or creates a dedicated RcloneDaemon for the given remote."""
        clean = remote_name.rstrip(":")
        if clean not in self.daemons:
            d = RcloneDaemon(remote_name=clean)
            d._integration_runner = self.runner
            d.state_changed.connect(lambda state, r=clean: self._handle_daemon_state(r, state))
            self.daemons[clean] = d
            self.tray.register_daemon(clean, d)
        return self.daemons[clean]

    def mount_remote(
        self,
        remote_name: str,
        read_only: bool = False,
        extra_args: Optional[list[str]] = None,
        startup_timeout: float = 15.0,
        retry_on_timeout: int = 0,
    ) -> None:
        """Starts mounting a specific cloud remote concurrently without unmounting others."""
        daemon = self.get_or_create_daemon(remote_name)
        daemon.start(
            remote_name,
            read_only=read_only,
            extra_args=extra_args,
            startup_timeout=startup_timeout,
            retry_on_timeout=retry_on_timeout,
        )

    def unmount_remote(self, remote_name: str, on_done: Optional[Callable[[], None]] = None) -> None:
        """Safely stops and unmounts the specified remote."""
        clean = remote_name.rstrip(":")
        daemon = self.daemons.get(clean)
        if daemon and daemon.is_running():
            self._stop_poller_for(clean)
            daemon.stop_async(on_done=on_done)
        elif on_done:
            on_done()

    def unmount_all(self, on_done: Optional[Callable[[], None]] = None) -> None:
        """Stops and unmounts all currently running cloud drives."""
        active = [d for d in self.daemons.values() if d.is_running()]
        if not active:
            if on_done:
                on_done()
            return

        for clean in list(self.stats_pollers.keys()):
            self._stop_poller_for(clean)

        remaining = len(active)

        def _step_done() -> None:
            nonlocal remaining
            remaining -= 1
            if remaining <= 0 and on_done:
                on_done()

        for d in active:
            d.stop_async(on_done=_step_done)

    def run(self) -> None:
        """
        Starts the application, displays the tray icon or window mode,
        checks updates in the background, and handles auto-mount.
        """
        if self.tray_available:
            self.tray.show()
        else:
            self.tray.show_window_mode()
            self._show_gnome_tray_tip()

        # Check for updates in background (non-blocking)
        try:
            self._update_worker = UpdateCheckWorker(force=False)
            self._update_worker.update_available.connect(self.tray.notify_update_available)
            self._update_worker.start()
        except Exception as exc:
            logger.debug("Failed to start background update checker: %s", exc)

        # Background scan of configured remotes
        self.tray.refresh_remotes(self._on_initial_remotes)

    def _show_gnome_tray_tip(self) -> None:
        """Shows a friendly explanation when system tray is unavailable (e.g. Vanilla GNOME)."""
        cfg = load_config()
        if cfg.get("gnome_tray_tip_dismissed"):
            return
        box = QMessageBox(
            QMessageBox.Icon.Information,
            tr("Modo Janela Ativado"),
            tr("O DucksDrive está sendo executado no Modo Janela porque este ambiente desktop (ex: GNOME padrão) não possui suporte ativo à bandeja do sistema.\n\nPara exibir o ícone na barra superior, você pode instalar a extensão 'AppIndicator and KStatusNotifierItem Support' no GNOME."),
            parent=None,
        )
        cb_dont_show = QCheckBox(tr("Não mostrar esta mensagem novamente"))
        box.setCheckBox(cb_dont_show)
        box.addButton(tr("Entendi"), QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if cb_dont_show.isChecked():
            cfg["gnome_tray_tip_dismissed"] = True
            save_config(cfg)

    def _on_initial_remotes(self, remotes: list[str]) -> None:
        """Runs in the GUI thread once the remotes have been listed in the background."""
        if not remotes:
            logger.info("No cloud remotes found. Triggering first-run setup dialog...")
            QTimer.singleShot(200, self.tray._show_new_remote_dialog)
        else:
            last_remote = str(load_config().get("last_remote") or "").rstrip(":")
            target_remote = last_remote if last_remote in remotes else remotes[0]
            logger.info("Auto-mounting remote: %s (with network retry)", target_remote)
            self.mount_remote(target_remote, startup_timeout=30.0, retry_on_timeout=2)

    def _signal_handler(self, sig: int, frame: Any) -> None:
        logger.info("Signal %d received, shutting down...", sig)
        QTimer.singleShot(0, self.shutdown)

    @Slot(str)
    def _handle_state_change(self, state: str) -> None:
        """Legacy slot: delegates to primary remote state handler."""
        active = self.daemon.current_remote or "default"
        self._handle_daemon_state(active, state)

    def _handle_daemon_state(self, remote_name: str, state: str) -> None:
        """Handles transitions between mount states for each specific remote."""
        clean = remote_name.rstrip(":")
        daemon = self.daemons.get(clean)
        if not daemon:
            return

        self.tray._on_state_changed(state, remote_name=clean)

        if state == "mounted":
            # Start statistics polling for this specific daemon
            if daemon.rc_credentials:
                self._stop_poller_for(clean)
                poller = RcloneStatsPoller(daemon.rc_credentials, daemon=daemon)
                poller.stats_updated.connect(lambda stats, r=clean: self.tray.update_stats_for_remote(r, stats))
                poller.connection_lost.connect(lambda r=clean: self._on_connection_lost(r))
                poller.start()
                self.stats_pollers[clean] = poller

            # Add to file manager sidebar and persist last_remote
            save_last_remote(clean)
            self.runner.submit(integrate_mount, daemon.mount_point, f"DucksDrive - {clean}")
            notify_mount_success(clean, daemon.mount_point, fallback_tray=self.tray)

        elif state in ("unmounting", "stopped", "error"):
            # Stop poller non-blocking
            self._stop_poller_for(clean)

            if state in ("stopped", "error"):
                # Clean sidebar integration
                self.runner.submit(clean_integration, daemon.mount_point)

    def _stop_poller_for(self, remote_name: str, wait: bool = False) -> None:
        """Detaches and stops the stats poller for a given remote."""
        clean = remote_name.rstrip(":")
        poller = self.stats_pollers.pop(clean, None)
        if poller is None:
            return
        poller.stop(wait=wait)
        if poller.isRunning():
            self._old_pollers.append(poller)
            poller.finished.connect(self._reap_pollers)

    def _stop_poller(self, wait: bool = False) -> None:
        """Legacy helper: stops all pollers."""
        for clean in list(self.stats_pollers.keys()):
            self._stop_poller_for(clean, wait=wait)

    @Slot()
    def _reap_pollers(self) -> None:
        self._old_pollers = [p for p in self._old_pollers if p.isRunning()]

    def _on_connection_lost(self, remote_name: str) -> None:
        logger.warning("Lost connection to the rclone RC API for '%s'.", remote_name)
        notify_connection_lost(remote_name, fallback_tray=self.tray)

    def _cleanup_threads(self) -> None:
        """Stops all background worker threads cleanly."""
        if self._threads_cleaned:
            return
        self._threads_cleaned = True
        for clean in list(self.stats_pollers.keys()):
            self._stop_poller_for(clean, wait=True)
        for poller in self._old_pollers:
            poller.wait(2000)
        if self._update_worker is not None and self._update_worker.isRunning():
            self._update_worker.wait(1000)
        self.tray.status_popup.shutdown_workers()
        self.tray.shutdown_workers()
        self.runner.wait_all()

    def shutdown(self) -> None:
        """Graceful shutdown: unmount all mounts and exit."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        logger.info("Shutting down application...")
        for clean in list(self.stats_pollers.keys()):
            self._stop_poller_for(clean, wait=True)
        for d in self.daemons.values():
            if d.is_running():
                d.stop()
        self._cleanup_threads()
        self.app.quit()


def main() -> None:
    """Main entrypoint function."""
    sys.argv[0] = "ducksdrive"
    app = QApplication(sys.argv)
    app.setApplicationName("DucksDrive")
    app.setApplicationDisplayName("DucksDrive")
    app.setDesktopFileName(os.environ.get("FLATPAK_ID") or "ducksdrive")
    app.setWindowIcon(get_app_icon())
    app.setQuitOnLastWindowClosed(False)

    SYSTEM_DEFAULT_PALETTE = QApplication.instance().palette()
    set_system_default_palette(SYSTEM_DEFAULT_PALETTE)

    # Initialize language, theme, and synchronize autostart once
    cfg = load_config()
    set_language(cfg.get("language", "pt_BR"))
    apply_app_theme(str(cfg.get("theme") or "auto"))
    _sync_autostart(bool(cfg.get("autostart", True)))

    # 1. Enforce Single Instance via QLockFile
    lock_dir = CONFIG_DIR
    os.makedirs(lock_dir, exist_ok=True)
    lock_file_path = os.path.join(lock_dir, "app.lock")

    lock_file = QLockFile(lock_file_path)
    lock_file.setStaleLockTime(30000)
    if not lock_file.tryLock(100):
        QMessageBox.critical(
            None,
            tr("Já em Execução"),
            tr("Uma instância do DucksDrive já está em execução na bandeja do sistema.")
        )
        sys.exit(1)

    # 2. System Tray Availability Check (Fast check with fallback to Window Mode for GNOME/Wayland)
    tray_available = False
    for _ in range(5):
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray_available = True
            break
        time.sleep(0.2)
        app.processEvents()

    if not tray_available:
        logger.info("System tray is not available. DucksDrive will open in Window Mode (GNOME Wayland/Vanilla compatible).")

    # 3. Version Check
    version = get_rclone_version()
    if not version:
        QMessageBox.critical(None, tr("Rclone Não Encontrado"),
                             tr("O Rclone não está instalado ou não foi encontrado no PATH."))
        sys.exit(1)

    if version < REQUIRED_RCLONE_VERSION:
        v_str = ".".join(map(str, version))
        req_str = ".".join(map(str, REQUIRED_RCLONE_VERSION))
        QMessageBox.warning(None, tr("Rclone Desatualizado"),
                            tr("Detectado Rclone v%s.\nA versão v%s ou superior é recomendada.") % (v_str, req_str))

    # 4. Initialize Controller
    controller = RcloneAppController(app, tray_available=tray_available)
    controller.run()

    # 5. Execute Loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
