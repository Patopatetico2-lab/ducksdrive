"""
ducksdrive/system/vfs_inhibitor.py - System shutdown & sleep inhibitor for active VFS write-back queues in DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Monitors pending upload queues in Rclone's VFS cache and takes out an inhibitor
lock via `systemd-inhibit` / org.freedesktop.login1 to prevent system shutdown
or sleep while asynchronous writeback is in progress, preventing data loss.
"""

import logging
import shutil
import subprocess
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from ducksdrive.i18n import tr
from ducksdrive.system.notifications import notify_info, notify_warning
from ducksdrive.core.rclone_config import get_clean_env

logger = logging.getLogger(__name__)


class VFSInhibitorManager(QObject):
    """
    Manages the lifecycle of a systemd/logind inhibitor lock.
    Holds a delay inhibitor on shutdown/sleep as long as VFS upload queues are non-empty.
    """

    lock_state_changed = Signal(bool, int)  # (is_locked, pending_count)

    def __init__(self, parent: Optional[QObject] = None, notify_on_release: bool = True) -> None:
        super().__init__(parent)
        self._is_locked: bool = False
        self._last_count: int = 0
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._inhibitor_bin: Optional[str] = shutil.which("systemd-inhibit")
        self._inhibitor_failed: bool = False
        self._notified_locked: bool = False
        self._notify_on_release: bool = notify_on_release

    @property
    def is_locked(self) -> bool:
        """True if a shutdown/sleep inhibition lock is actively held."""
        if not self._is_locked:
            return False
        if self._process is not None:
            return self._process.poll() is None
        return True

    @property
    def pending_count(self) -> int:
        """Returns the number of files currently pending upload in the VFS cache."""
        return self._last_count if self._is_locked else 0

    def acquire_lock(self, pending_count: int, reason: Optional[str] = None) -> bool:
        """
        Acquires a systemd/logind inhibitor lock to delay shutdown and sleep.
        """
        self._last_count = max(0, pending_count)
        if self.is_locked:
            self.lock_state_changed.emit(True, self._last_count)
            return True

        # If a previous systemd-inhibit process exited immediately with an error (e.g. sandbox/D-Bus denied),
        # avoid respawning and spamming notifications every second.
        if self._process is not None and self._process.poll() is not None:
            rc = self._process.returncode
            self._process = None
            if rc != 0:
                logger.warning("systemd-inhibit exited with status %d; disabling inhibitor retries for this session.", rc)
                self._inhibitor_failed = True
                self._is_locked = False
                self.lock_state_changed.emit(False, 0)
                return False

        if self._inhibitor_failed:
            return False

        why_msg = reason or tr("Gravando %d arquivo(s) pendentes no cache VFS para a nuvem") % self._last_count

        if self._inhibitor_bin:
            try:
                cmd = [
                    self._inhibitor_bin,
                    "--what=shutdown:sleep",
                    "--who=DucksDrive",
                    f"--why={why_msg}",
                    "--mode=delay",
                    "sleep",
                    "infinity",
                ]
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=get_clean_env(include_keyring=False),
                )
                self._is_locked = True
                logger.info("Acquired systemd inhibitor lock (PID %d) for %d pending VFS uploads.", self._process.pid, self._last_count)
            except Exception as exc:
                logger.warning("Failed to launch systemd-inhibit: %s", exc)
                self._inhibitor_failed = True
                self._is_locked = False
                self._process = None
        else:
            # Fallback when systemd-inhibit binary is absent (e.g. non-systemd or container)
            logger.info("systemd-inhibit not available in PATH; simulated lock active.")
            self._is_locked = True

        if self._is_locked:
            self.lock_state_changed.emit(True, self._last_count)
            if not self._notified_locked:
                self._notified_locked = True
                try:
                    notify_warning(
                        tr("Proteção de Desligamento Ativa"),
                        tr("O desligamento foi adiado para evitar perda de dados (%d arquivo(s) no cache VFS).") % self._last_count,
                    )
                except Exception as exc:
                    logger.debug("Failed to send notification: %s", exc)
            return True

        return False

    def release_lock(self, notify: bool = True) -> bool:
        """
        Releases the held inhibitor lock.
        """
        if not self._is_locked and self._process is None:
            return True

        logger.info("Releasing systemd inhibitor lock (had %d pending VFS uploads).", self._last_count)
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=1)
                except Exception:
                    pass
            self._process = None

        self._is_locked = False
        self._notified_locked = False
        prev_count = self._last_count
        self._last_count = 0
        self.lock_state_changed.emit(False, 0)

        if notify and self._notify_on_release and prev_count > 0:
            try:
                notify_info(
                    tr("Sincronização Concluída"),
                    tr("Todos os arquivos do cache VFS foram gravados na nuvem. O bloqueio de desligamento foi liberado."),
                )
            except Exception as exc:
                logger.debug("Failed to send notification: %s", exc)

        return True

    def update_queue_status(self, total_pending: int, remote_names: Optional[List[str]] = None) -> None:
        """
        Called when telemetry updates. Automatically acquires lock if total_pending > 0,
        or releases lock if total_pending == 0.
        """
        if total_pending > 0:
            self.acquire_lock(total_pending)
        else:
            if self._is_locked:
                self.release_lock(notify=True)

    def shutdown(self) -> None:
        """Cleanly releases any held lock upon application exit."""
        self.release_lock(notify=False)
