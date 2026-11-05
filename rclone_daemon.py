"""
rclone_daemon.py - Virtual drive lifecycle and FUSE mount daemon.

Manages the background `rclone mount` subprocess with VFS cache controls,
random in-memory Remote Control (RC) credentials, and robust FUSE unmounting
with dynamic fallback to fusermount3 or fusermount.
"""

import base64
import glob
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QCoreApplication, QThread

from i18n import tr
from rclone_config import get_clean_env, get_remote_type_fast

logger = logging.getLogger(__name__)

DEFAULT_MOUNT_POINT = os.path.expanduser("~/CloudDrives")
DEFAULT_RC_HOST = "127.0.0.1"
DEFAULT_RC_PORT = 5572
DEFAULT_STARTUP_TIMEOUT = 30.0
xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
LOG_DIR = os.path.join(xdg_data, "ducksdrive")


@dataclass(frozen=True)
class RCCredentials:
    """Stores in-memory credentials and connection details for Rclone RC API."""
    user: str
    password: str
    host: str = DEFAULT_RC_HOST
    port: int = DEFAULT_RC_PORT
    auth_header: str = ""

    def __post_init__(self) -> None:
        if not self.auth_header:
            b64_auth = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
            object.__setattr__(self, "auth_header", f"Basic {b64_auth}")

    @property
    def url(self) -> str:
        """Returns the base HTTP URL for the RC server."""
        return f"http://{self.host}:{self.port}"


def generate_rc_credentials(host: str = DEFAULT_RC_HOST, port: int = DEFAULT_RC_PORT) -> RCCredentials:
    """
    Generates cryptographically secure, random in-memory credentials for the RC interface.

    Args:
        host: Host binding for the RC server.
        port: Port number for the RC server.

    Returns:
        RCCredentials: In-memory credentials object.
    """
    user = f"rc_user_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    return RCCredentials(user=user, password=password, host=host, port=port)


def get_available_rc_port(host: str = DEFAULT_RC_HOST, preferred_port: int = DEFAULT_RC_PORT) -> int:
    """Checks if preferred_port is free on host; if in use, selects an available localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, preferred_port))
            return preferred_port
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def get_unmount_binary() -> Optional[str]:
    """
    Finds the appropriate FUSE unmount tool available on the system.
    Prioritizes fusermount3, then fusermount, then umount.

    Returns:
        Optional[str]: Path to the unmount executable, or None if none found.
    """
    for binary_name in ("fusermount3", "fusermount", "umount"):
        path = shutil.which(binary_name)
        if path:
            return path
    return None


def unmount_fuse_path(mount_path: str, lazy: bool = False, fallback_lazy: bool = True) -> bool:
    """
    Unmounts a FUSE mount path using the dynamically detected unmount tool.

    Args:
        mount_path: Directory path to unmount.
        lazy: Whether to attempt lazy unmount immediately.
        fallback_lazy: Whether to retry with lazy unmount (-uz or -l) if normal unmount fails.

    Returns:
        bool: True if unmounted successfully or path wasn't mounted, False otherwise.
    """
    mount_path = os.path.abspath(os.path.expanduser(mount_path))
    if not is_path_mounted(mount_path):
        return True

    unmount_bin = get_unmount_binary()
    if not unmount_bin:
        logger.error("No unmount tool (fusermount3, fusermount, umount) found in PATH.")
        return False

    tool_name = os.path.basename(unmount_bin)
    cmd: List[str] = [unmount_bin]

    if "fusermount" in tool_name:
        cmd.extend(["-uz" if lazy else "-u", mount_path])
    else:  # standard umount
        if lazy:
            cmd.extend(["-l", mount_path])
        else:
            cmd.append(mount_path)

    logger.info("Executing unmount: %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=get_clean_env())
        if res.returncode == 0:
            logger.info("Successfully unmounted %s", mount_path)
            return True
        logger.warning("Unmount failed with return code %d: %s", res.returncode, res.stderr.strip())
    except Exception as exc:
        logger.error("Error executing unmount: %s", exc)

    # Retry with lazy unmount if first attempt failed and fallback_lazy is enabled
    if not lazy and fallback_lazy:
        lazy_flag = "-uz" if "fusermount" in tool_name else "-l"
        logger.info("Retrying with lazy unmount (%s)...", lazy_flag)
        try:
            lazy_res = subprocess.run(
                [unmount_bin, lazy_flag, mount_path],
                capture_output=True,
                text=True,
                timeout=5,
                env=get_clean_env(),
            )
            return lazy_res.returncode == 0
        except Exception as exc:
            logger.error("Lazy unmount failed: %s", exc)

    return False


def unescape_mount_path(p: str) -> str:
    """Unescapes octal and special character sequences in /proc/mounts paths (e.g. \040)."""
    return (
        p.replace(r"\040", " ")
         .replace(r"\011", "\t")
         .replace(r"\012", "\n")
         .replace(r"\134", "\\")
    )


def prune_old_logs(keep: int = 5) -> None:
    """Keeps only the `keep` most recent per-mount rclone logs in LOG_DIR."""
    try:
        files = sorted(glob.glob(os.path.join(LOG_DIR, "rclone-*.log")), key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception as exc:
        logger.debug("Failed to prune old rclone logs: %s", exc)


def is_path_mounted(path: str) -> bool:
    """
    Checks if a given path is an active mount point.

    Args:
        path: Path to check.

    Returns:
        bool: True if the path is an active mount point.
    """
    path = os.path.abspath(os.path.expanduser(path))
    real_parent = os.path.realpath(os.path.dirname(path))
    real_target = os.path.normpath(os.path.join(real_parent, os.path.basename(path)))
    targets = {path, real_target}

    # Read /proc/mounts first (in-memory kernel procfs; never blocks on FUSE stat in D-state)
    if os.path.exists("/proc/mounts"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mount_point_in_proc = os.path.abspath(unescape_mount_path(parts[1]))
                        if mount_point_in_proc in targets:
                            return True
            return False
        except Exception:
            pass

    return os.path.ismount(path) or (real_target != path and os.path.ismount(real_target))


def _unpause_rc_before_stop(creds: Optional[RCCredentials]) -> None:
    """Restores unlimited bandwidth on a user-paused mount before shutdown so VFS metadata flush never stalls."""
    if creds is None:
        return
    try:
        req = urllib.request.Request(
            url=f"{creds.url}/core/bwlimit",
            data=json.dumps({"rate": "off"}).encode("utf-8"),
            headers={
                "Authorization": creds.auth_header,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.5):
            pass
    except Exception:
        pass


def _teardown_mount(
    process: Optional["subprocess.Popen[str]"],
    mount_point: str,
    paused_creds: Optional[RCCredentials] = None,
) -> bool:
    """
    Blocking part of stopping a mount: unpause RC if needed, unmount the FUSE path, terminate rclone
    (15s grace period for the VFS cache metadata flush, then SIGKILL) and run a final lazy unmount
    if the path is still reported as mounted.

    Takes up to ~25s in the worst case, so it must never run on the GUI thread
    (except during application shutdown). Touches no Qt objects.

    Returns:
        bool: True if the first (non-lazy) unmount succeeded.
    """
    # 0. If user had paused transfers, restore bandwidth before flushing and unmounting
    if paused_creds is not None and process and process.poll() is None:
        _unpause_rc_before_stop(paused_creds)

    # 1. First attempt a clean (non-lazy) unmount via FUSE tool without forcing -uz while rclone is flushing
    unmounted = unmount_fuse_path(mount_point, lazy=False, fallback_lazy=False)

    # 2. Terminate the subprocess with extended timeout (15s) for safe VFS metadata flushing
    if process and process.poll() is None:
        try:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.warning("Process did not terminate within 15s, sending SIGKILL...")
                process.kill()
                process.wait(timeout=3)
        except Exception as exc:
            logger.warning("Error terminating rclone process: %s", exc)

    # 3. Final fallback unmount if still reported as mounted
    if is_path_mounted(mount_point):
        unmounted = unmount_fuse_path(mount_point, lazy=True) or unmounted

    return unmounted


class _TeardownWorker(QThread):
    """Runs `_teardown_mount` in a background thread so the GUI never freezes while unmounting."""

    def __init__(
        self,
        process: Optional["subprocess.Popen[str]"],
        mount_point: str,
        parent: Optional[QObject] = None,
        paused_creds: Optional[RCCredentials] = None,
    ) -> None:
        super().__init__(parent)
        self._process = process
        self._mount_point = mount_point
        self._paused_creds = paused_creds
        self.unmounted = False

    def run(self) -> None:
        try:
            self.unmounted = _teardown_mount(self._process, self._mount_point, self._paused_creds)
        except Exception as exc:
            logger.error("Unexpected error while tearing down mount: %s", exc)
            self.unmounted = False


class RcloneDaemon(QObject):
    """
    Manages the lifecycle of the `rclone mount` process, including
    secure in-memory RC credentials, VFS cache settings, and safe shutdown.
    """

    state_changed = Signal(str)  # 'stopped', 'starting', 'mounted', 'unmounting', 'error'
    mount_error = Signal(str)

    def __init__(
        self,
        mount_point: str = DEFAULT_MOUNT_POINT,
        rc_host: str = DEFAULT_RC_HOST,
        rc_port: int = DEFAULT_RC_PORT,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.mount_point = os.path.abspath(os.path.expanduser(mount_point))
        self.rc_host = rc_host
        self.rc_port = rc_port
        self.rc_credentials: Optional[RCCredentials] = None
        self.process: Optional[subprocess.Popen[str]] = None
        self.current_remote: Optional[str] = None
        self.current_read_only: bool = False
        self.current_extra_args: Optional[List[str]] = None
        self.state = "stopped"
        self.is_user_paused = False
        self._startup_timer: Optional[QTimer] = None
        self._retry_timer: Optional[QTimer] = None
        self._retry_pending = False
        self._startup_start_time = 0.0
        self._log_file: Optional[Any] = None

        # Asynchronous shutdown bookkeeping (see stop_async / stop)
        self._stop_worker: Optional[_TeardownWorker] = None
        self._stop_callbacks: List[Callable[[], None]] = []
        self._last_stop_result = True
        self._pending_start: Optional[tuple[str, Optional[List[str]], bool, float, int]] = None
        self._pre_unmount_attempted = False

        # Active health monitoring timer for background FUSE process
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(2000)
        self.health_timer.timeout.connect(self._check_process_health)
        self.health_timer.start()

        # Use Qt lifecycle signal aboutToQuit instead of atexit to avoid deleted C++ object errors
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.stop)

    def _set_state(self, new_state: str) -> None:
        self.state = new_state
        self.state_changed.emit(new_state)

    def _cancel_retry_timer(self) -> None:
        """Stops and disposes of any pending startup retry timer."""
        self._retry_pending = False
        if self._retry_timer is not None:
            self._retry_timer.stop()
            self._retry_timer.deleteLater()
            self._retry_timer = None

    def _schedule_retry(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """Schedules a cancellable single-shot retry timer."""
        self._cancel_retry_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._retry_timer = timer

        def _fire() -> None:
            self._retry_timer = None
            timer.deleteLater()
            callback()

        timer.timeout.connect(_fire)
        timer.start(delay_ms)

    def _check_process_health(self) -> None:
        """Actively monitors if the rclone mount process died unexpectedly in background."""
        if self.state == "mounted" and self.process is not None:
            if self.process.poll() is not None:
                if self.process.returncode == 0 and not is_path_mounted(self.mount_point):
                    logger.info("Mount point unmounted externally (clean exit). Updating state to stopped.")
                    self.stop_async()
                    return
                logger.error("Rclone mount process died unexpectedly in background!")
                # stop_async() flips the state to 'unmounting' right away, so this check cannot re-trigger
                # while the (background) teardown runs. The error state is applied once it finishes.
                self._stop_then_fail(tr("Rclone mount process died unexpectedly."))

    def _stop_then_fail(self, message: str) -> None:
        """Stops the mount in the background, then reports `message` as an error keeping the failed remote name."""
        failed_remote = self.current_remote
        failed_ro = self.current_read_only
        failed_args = self.current_extra_args

        def _report() -> None:
            self.current_remote = failed_remote
            self.current_read_only = failed_ro
            self.current_extra_args = failed_args
            self._set_state("error")
            self.mount_error.emit(message)

        self.stop_async(on_done=_report)

    def start(
        self,
        remote_name: str,
        extra_args: Optional[List[str]] = None,
        read_only: bool = False,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        retry_on_timeout: int = 0,
    ) -> bool:
        """
        Starts the `rclone mount` process for the specified remote.

        Args:
            remote_name: Name of the remote (e.g., 'gdrive' or 'gdrive:').
            extra_args: Optional additional CLI flags for rclone.
            read_only: If True, mounts the remote in read-only mode.
            startup_timeout: Maximum seconds to wait for FUSE mount to become active.
            retry_on_timeout: Number of automatic retries if mount times out (e.g. during system boot).
        Returns:
            bool: True if the mount was started (or queued behind a running shutdown), False otherwise.
        """
        self._cancel_retry_timer()

        if self.state in ("mounted", "starting", "unmounting") or self.is_running():
            # Never block the GUI waiting for the old mount to go away: shut it down in the
            # background and start the requested remote as soon as that finishes.
            logger.info("Another mount is active. Stopping current mount before starting '%s'", remote_name)
            self._pending_start = (remote_name, extra_args, read_only, startup_timeout, retry_on_timeout)
            self.stop_async(on_done=self._run_pending_start)
            return True

        if hasattr(self, "_startup_timer") and self._startup_timer:
            self._startup_timer.stop()
            self._startup_timer.deleteLater()
            self._startup_timer = None

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            err = tr("Rclone executable not found in PATH.")
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        # Set dynamic mount point based on remote: ~/CloudDrives/{remote_name}
        clean_remote_name = remote_name.rstrip(":")
        self.mount_point = os.path.abspath(os.path.expanduser(f"~/CloudDrives/{clean_remote_name}"))

        # Ensure directory is unmounted before mounting (off the GUI thread via _TeardownWorker)
        if is_path_mounted(self.mount_point) and not self._pre_unmount_attempted:
            logger.info("Mount point is currently busy, attempting async pre-mount unmount...")
            self._pre_unmount_attempted = True
            self._pending_start = (remote_name, extra_args, read_only, startup_timeout, retry_on_timeout)
            if self._run_pending_start not in self._stop_callbacks:
                self._stop_callbacks.append(self._run_pending_start)
            _, mp, paused_creds = self._begin_stop()
            worker = _TeardownWorker(None, mp, self, paused_creds)
            self._stop_worker = worker
            worker.finished.connect(self._on_stop_worker_finished)
            worker.start()
            return True
        self._pre_unmount_attempted = False

        # Prepare target mount folder
        try:
            os.makedirs(self.mount_point, exist_ok=True)
        except OSError as exc:
            err = tr("Falha ao criar o diretório de montagem '%s': %s") % (self.mount_point, exc)
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        # Generate fresh in-memory RC credentials on an available port
        active_port = get_available_rc_port(self.rc_host, self.rc_port)
        self.rc_credentials = generate_rc_credentials(self.rc_host, active_port)
        self.current_remote = remote_name
        self.current_read_only = read_only
        self.current_extra_args = list(extra_args) if extra_args else None
        self.is_user_paused = False

        formatted_remote = remote_name if remote_name.endswith(":") else f"{remote_name}:"

        # Load dynamic configuration preferences
        from rclone_settings import load_config
        cfg = load_config()
        bwlimit = str(cfg.get("bwlimit", "off"))
        cache_size = str(cfg.get("vfs_cache_max_size", "2G"))
        try:
            transfers_int = max(1, int(cfg.get("transfers", 4)))
        except (ValueError, TypeError):
            transfers_int = 4
        transfers = str(transfers_int)
        checkers = str(max(8, transfers_int * 2))
        vfs_cache_mode = str(cfg.get("vfs_cache_mode", "full"))

        # Build mandatory rclone mount arguments with user settings and FUSE/VFS optimizations
        cmd = [
            rclone_bin,
            "mount",
            formatted_remote,
            self.mount_point,
            "--vfs-cache-mode",
            vfs_cache_mode,
            "--vfs-cache-max-size",
            cache_size,
            "--vfs-cache-max-age",
            "24h",
            "--vfs-write-back",
            "5s",
            "--vfs-read-ahead",
            "128M",
            "--vfs-fast-fingerprint",
            "--vfs-read-chunk-size",
            "8M",
            "--vfs-read-chunk-size-limit",
            "off",
            "--dir-cache-time",
            "30m",
            "--poll-interval",
            "15s",
            "--attr-timeout",
            "10s",
            "--write-back-cache",
            "--checkers",
            checkers,
        ]
        if get_remote_type_fast(clean_remote_name) == "drive":
            cmd.extend([
                "--drive-pacer-min-sleep",
                "10ms",
                "--drive-pacer-burst",
                "200",
                "--drive-chunk-size",
                "32M",
            ])
        try:
            if os.path.isdir(self.mount_point) and os.listdir(self.mount_point):
                logger.warning("Mount directory '%s' is not empty; passing --allow-non-empty.", self.mount_point)
                cmd.append("--allow-non-empty")
        except OSError:
            pass
        if bwlimit.lower() != "off":
            cmd.extend(["--bwlimit", bwlimit])
        cmd.extend([
            "--transfers",
            transfers,
            "--rc",
            "--rc-addr",
            f"{self.rc_credentials.host}:{self.rc_credentials.port}",
        ])
        
        if read_only:
            cmd.append("--read-only")

        if extra_args:
            cmd.extend(extra_args)

        self._set_state("starting")
        logger.info("Starting rclone mount: %s", " ".join(cmd))

        # Open dedicated log file to prevent stdout/stderr pipe deadlock
        os.makedirs(LOG_DIR, exist_ok=True)
        prune_old_logs(keep=5)
        log_path = os.path.join(LOG_DIR, f"rclone-{clean_remote_name}-{int(time.time())}.log")
        try:
            self._log_file = open(log_path, "w", encoding="utf-8")
        except Exception as exc:
            err = tr("Falha ao abrir o arquivo de log do Rclone '%s': %s") % (log_path, exc)
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        try:
            clean_env = get_clean_env()
            clean_env["RCLONE_RC_USER"] = self.rc_credentials.user
            clean_env["RCLONE_RC_PASS"] = self.rc_credentials.password
            self.process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                text=True,
                env=clean_env,
            )
        except Exception as exc:
            err = tr("Falha ao iniciar o processo de montagem do Rclone: %s") % exc
            logger.error(err)
            self.mount_error.emit(err)
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self._set_state("error")
            return False

        # Asynchronous non-blocking startup check using QTimer (avoids freezing GUI)
        self._startup_start_time = time.time()
        startup_timer = QTimer(self)
        self._startup_timer = startup_timer
        startup_timer.setInterval(500)

        def _dispose_startup_timer() -> None:
            startup_timer.stop()
            startup_timer.deleteLater()
            if self._startup_timer is startup_timer:
                self._startup_timer = None

        def check_startup() -> None:
            if not self.process or self.state != "starting":
                _dispose_startup_timer()
                return

            if self.process.poll() is not None:
                _dispose_startup_timer()
                log_content = ""
                if self._log_file:
                    try:
                        self._log_file.flush()
                        with open(log_path, "r", encoding="utf-8") as lf:
                            log_content = lf.read()
                    except Exception:
                        pass

                # Check if process exited due to temporary network/DNS unreadiness at boot
                is_net_err = any(kw in log_content.lower() for kw in (
                    "no such host", "network is unreachable", "connection refused",
                    "dial tcp", "temporary failure in name resolution", "context deadline exceeded"
                ))
                if retry_on_timeout > 0 and is_net_err:
                    logger.warning(
                        "Rclone mount exited due to network unreadiness at startup. Retrying in 4s (%d retries left)...",
                        retry_on_timeout,
                    )
                    def _retry_on_net() -> None:
                        self.start(
                            remote_name,
                            extra_args=extra_args,
                            read_only=read_only,
                            startup_timeout=startup_timeout,
                            retry_on_timeout=retry_on_timeout - 1,
                        )
                    def _on_stopped_net() -> None:
                        if self._retry_pending:
                            self._schedule_retry(4000, _retry_on_net)
                    self.stop_async(on_done=_on_stopped_net)
                    self._retry_pending = True
                    return

                err = tr("A montagem do Rclone encerrou durante a inicialização:\n%s") % log_content.strip()
                logger.error(err)
                self._stop_then_fail(err)
                return

            if is_path_mounted(self.mount_point):
                _dispose_startup_timer()
                self._set_state("mounted")
                return

            if time.time() - self._startup_start_time > startup_timeout:
                _dispose_startup_timer()
                if retry_on_timeout > 0:
                    logger.warning(
                        "Rclone mount timed out after %.0fs for path '%s'. Network might be slow/connecting; retrying in 3s (%d retries left)...",
                        startup_timeout,
                        self.mount_point,
                        retry_on_timeout,
                    )
                    def _retry_start() -> None:
                        self.start(
                            remote_name,
                            extra_args=extra_args,
                            read_only=read_only,
                            startup_timeout=startup_timeout,
                            retry_on_timeout=retry_on_timeout - 1,
                        )
                    def _on_stopped_timeout() -> None:
                        if self._retry_pending:
                            self._schedule_retry(3000, _retry_start)
                    self.stop_async(on_done=_on_stopped_timeout)
                    self._retry_pending = True
                    return

                err = tr("A montagem do Rclone excedeu o tempo limite de %ds para o caminho '%s'.") % (
                    int(startup_timeout),
                    self.mount_point,
                )
                logger.error(err)
                self._stop_then_fail(err)
                return

        startup_timer.timeout.connect(check_startup)
        startup_timer.start()
        return True

    def _run_pending_start(self) -> None:
        """Starts the remote queued by start() once the previous mount has been torn down."""
        pending, self._pending_start = self._pending_start, None
        if pending is not None:
            remote_name, extra_args, read_only, startup_timeout, retry_on_timeout = pending
            self.start(remote_name, extra_args, read_only, startup_timeout, retry_on_timeout)

    def _begin_stop(self) -> tuple[Optional["subprocess.Popen[str]"], str, Optional[RCCredentials]]:
        """GUI-thread half of stopping: flips the state and halts the startup timer."""
        self._cancel_retry_timer()
        self._set_state("unmounting")
        logger.info("Stopping rclone mount on %s", self.mount_point)

        if hasattr(self, "_startup_timer") and self._startup_timer:
            self._startup_timer.stop()
            self._startup_timer.deleteLater()
            self._startup_timer = None

        paused_creds = self.rc_credentials if self.is_user_paused else None
        return self.process, self.mount_point, paused_creds

    def _finish_stop(self, unmounted: bool) -> bool:
        """GUI-thread half of stopping: releases resources, publishes 'stopped' and runs waiting callbacks."""
        # Close dedicated log file
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        self.process = None
        self.rc_credentials = None
        self.current_remote = None
        self.current_read_only = False
        self.current_extra_args = None
        self.is_user_paused = False
        if self._pending_start is None:
            self._pre_unmount_attempted = False
        self._last_stop_result = unmounted
        self._set_state("stopped")

        callbacks, self._stop_callbacks = self._stop_callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.exception("Error in stop callback")
        return unmounted

    @Slot()
    def _on_stop_worker_finished(self) -> None:
        worker = self._stop_worker
        if worker is None:
            return  # already finalized by a blocking stop()
        self._stop_worker = None
        unmounted = worker.unmounted
        worker.deleteLater()
        self._finish_stop(unmounted)

    def stop_async(self, on_done: Optional[Callable[[], None]] = None) -> None:
        """
        Stops the mount without blocking the GUI thread.

        The unmount / rclone termination (up to ~20s) runs in a worker thread; the state
        goes 'unmounting' immediately and 'stopped' when it finishes. `on_done` (if given)
        is called on the GUI thread right after the 'stopped' state is published, so several
        callers can safely wait on the same shutdown.
        """
        self._cancel_retry_timer()
        if on_done is not self._run_pending_start:
            self._pending_start = None
            self._stop_callbacks = [cb for cb in self._stop_callbacks if cb != self._run_pending_start]

        if self.state == "stopped":
            if on_done is not None:
                on_done()
            return

        if on_done is not None:
            if on_done is not self._run_pending_start or self._run_pending_start not in self._stop_callbacks:
                self._stop_callbacks.append(on_done)

        if self.state == "unmounting":
            return  # a shutdown is already running; on_done fires when it ends

        process, mount_point, paused_creds = self._begin_stop()
        worker = _TeardownWorker(process, mount_point, self, paused_creds)
        self._stop_worker = worker
        worker.finished.connect(self._on_stop_worker_finished)
        worker.start()

    def stop(self) -> bool:
        """
        Safely stops the rclone mount process and unmounts the FUSE filesystem, BLOCKING until done.

        Only use it where blocking is intended (application shutdown, uninstall).
        Everywhere else prefer `stop_async()`.

        Returns:
            bool: True if stopped and unmounted cleanly.
        """
        self._cancel_retry_timer()
        self._pending_start = None
        self._stop_callbacks = [cb for cb in self._stop_callbacks if cb != self._run_pending_start]

        if self.state == "stopped":
            return True

        if self.state == "unmounting":
            # An async shutdown is in flight: wait for it instead of starting another one.
            worker = self._stop_worker
            if worker is not None:
                worker.wait()
                self._on_stop_worker_finished()
            return self._last_stop_result

        process, mount_point, paused_creds = self._begin_stop()
        unmounted = _teardown_mount(process, mount_point, paused_creds)
        return self._finish_stop(unmounted)

    def is_running(self) -> bool:
        """Checks if the mount process is actively running and path is mounted."""
        return self.process is not None and self.process.poll() is None

    def refresh_vfs(self, timeout: float = 15.0) -> bool:
        """Sends POST /vfs/refresh to active daemon via RC API. Blocking call."""
        if not self.is_running() or not self.rc_credentials:
            return False
        from rclone_stats import rc_vfs_refresh
        return rc_vfs_refresh(self.rc_credentials, timeout=timeout)
