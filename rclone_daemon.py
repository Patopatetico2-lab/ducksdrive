"""
rclone_daemon.py - Virtual drive lifecycle and FUSE mount daemon.

Manages the background `rclone mount` subprocess with VFS cache controls,
random in-memory Remote Control (RC) credentials, and robust FUSE unmounting
with dynamic fallback to fusermount3 or fusermount.
"""

import atexit
import logging
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Fallback-safe PySide6 imports
try:
    from PySide6.QtCore import QObject, Signal, QTimer
except ImportError:  # pragma: no cover
    QObject = object  # type: ignore

    def Signal(*args: Any) -> Any:  # type: ignore
        """Dummy signal placeholder if PySide6 is not yet installed."""
        class _Signal:
            def emit(self, *a: Any, **kw: Any) -> None:
                pass

            def connect(self, slot: Any) -> None:
                pass
        return _Signal()

    class QTimer:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
        def setInterval(self, ms: int) -> None:
            pass
        def start(self) -> None:
            pass
        def stop(self) -> None:
            pass


DEFAULT_MOUNT_POINT = os.path.expanduser("~/GoogleDrive")
DEFAULT_RC_HOST = "127.0.0.1"
DEFAULT_RC_PORT = 5572
DEFAULT_VFS_CACHE_MODE = "full"
DEFAULT_VFS_CACHE_MAX_SIZE = "10G"


@dataclass(frozen=True)
class RCCredentials:
    """Stores in-memory credentials and connection details for Rclone RC API."""
    user: str
    password: str
    host: str = DEFAULT_RC_HOST
    port: int = DEFAULT_RC_PORT

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


def unmount_fuse_path(mount_path: str, lazy: bool = False) -> bool:
    """
    Unmounts a FUSE mount path using the dynamically detected unmount tool.

    Args:
        mount_path: Directory path to unmount.
        lazy: Whether to attempt lazy unmount if busy.

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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            logger.info("Successfully unmounted %s", mount_path)
            return True
        logger.warning("Unmount failed with return code %d: %s", res.returncode, res.stderr.strip())
    except Exception as exc:
        logger.error("Error executing unmount: %s", exc)

    # Retry with lazy unmount if first attempt failed and not already lazy
    if not lazy and "fusermount" in tool_name:
        logger.info("Retrying with lazy unmount (-uz)...")
        try:
            lazy_res = subprocess.run([unmount_bin, "-uz", mount_path], capture_output=True, text=True, timeout=5)
            return lazy_res.returncode == 0
        except Exception as exc:
            logger.error("Lazy unmount failed: %s", exc)

    return False


def is_path_mounted(path: str) -> bool:
    """
    Checks if a given path is an active mount point.

    Args:
        path: Path to check.

    Returns:
        bool: True if the path is an active mount point.
    """
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.ismount(path):
        return True

    # Check /proc/mounts as fallback for FUSE mounts
    if os.path.exists("/proc/mounts"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == path:
                        return True
        except Exception:
            pass

    return False


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
        self.state = "stopped"

        # Active health monitoring timer for background FUSE process
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(2000)
        self.health_timer.timeout.connect(self._check_process_health)
        self.health_timer.start()

        # Register exit handler for clean shutdown
        atexit.register(self.stop)

    def _set_state(self, new_state: str) -> None:
        self.state = new_state
        self.state_changed.emit(new_state)

    def _check_process_health(self) -> None:
        """Actively monitors if the rclone mount process died unexpectedly in background."""
        if self.state == "mounted" and self.process is not None:
            if self.process.poll() is not None:
                logger.error("Rclone mount process died unexpectedly in background!")
                self.mount_error.emit("Rclone mount process died unexpectedly.")
                self._set_state("error")
                self.stop()

    def start(self, remote_name: str, extra_args: Optional[List[str]] = None) -> bool:
        """
        Starts the `rclone mount` process for the specified remote.

        Args:
            remote_name: Name of the remote (e.g., 'gdrive' or 'gdrive:').
            extra_args: Optional additional CLI flags for rclone.

        Returns:
            bool: True if mount started successfully, False otherwise.
        """
        if self.is_running():
            logger.warning("Mount daemon is already running.")
            return True

        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            err = "Rclone executable not found in PATH."
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        # Prepare target mount folder
        try:
            os.makedirs(self.mount_point, exist_ok=True)
        except OSError as exc:
            err = f"Failed to create mount directory '{self.mount_point}': {exc}"
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        # Ensure directory is unmounted before mounting
        if is_path_mounted(self.mount_point):
            logger.info("Mount point is currently busy, attempting pre-mount unmount...")
            unmount_fuse_path(self.mount_point, lazy=True)

        # Generate fresh in-memory RC credentials
        self.rc_credentials = generate_rc_credentials(self.rc_host, self.rc_port)
        self.current_remote = remote_name

        formatted_remote = remote_name if remote_name.endswith(":") else f"{remote_name}:"

        # Build mandatory rclone mount arguments with VFS cache and network limiters
        cmd = [
            rclone_bin,
            "mount",
            formatted_remote,
            self.mount_point,
            "--vfs-cache-mode",
            DEFAULT_VFS_CACHE_MODE,
            "--vfs-cache-max-size",
            DEFAULT_VFS_CACHE_MAX_SIZE,
            "--bwlimit",
            "25M",
            "--transfers",
            "3",
            "--rc",
            "--rc-addr",
            f"{self.rc_credentials.host}:{self.rc_credentials.port}",
            "--rc-user",
            self.rc_credentials.user,
            "--rc-pass",
            self.rc_credentials.password,
        ]

        if extra_args:
            cmd.extend(extra_args)

        self._set_state("starting")
        # Mask credentials in logs for security
        masked_cmd = [
            arg if not (i > 0 and cmd[i - 1] in ("--rc-pass", "--rc-user")) else "******"
            for i, arg in enumerate(cmd)
        ]
        logger.info("Starting rclone mount: %s", " ".join(masked_cmd))

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Polling loop (up to 10 seconds, checking every 0.5s) for successful mount establishment
            start_time = time.time()
            mounted = False
            while time.time() - start_time < 10.0:
                if self.process.poll() is not None:
                    _, stderr = self.process.communicate()
                    err = f"Rclone mount exited during startup:\n{stderr.strip()}"
                    logger.error(err)
                    self.mount_error.emit(err)
                    self._set_state("error")
                    return False
                if is_path_mounted(self.mount_point):
                    mounted = True
                    break
                time.sleep(0.5)

            if not mounted:
                err = f"Rclone mount timed out after 10s for path '{self.mount_point}'."
                logger.error(err)
                self.mount_error.emit(err)
                self.stop()
                self._set_state("error")
                return False

            self._set_state("mounted")
            return True

        except Exception as exc:
            err = f"Failed to spawn rclone mount: {exc}"
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

    def stop(self) -> bool:
        """
        Safely stops the rclone mount process and unmounts the FUSE filesystem.

        Returns:
            bool: True if stopped and unmounted cleanly.
        """
        if self.state in ("stopped", "unmounting"):
            return True

        self._set_state("unmounting")
        logger.info("Stopping rclone mount on %s", self.mount_point)

        # 1. First attempt to unmount via FUSE tool
        unmounted = unmount_fuse_path(self.mount_point, lazy=False)

        # 2. Terminate the subprocess with extended timeout (10s) for safe VFS cache flushing
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("Process did not terminate within 10s, sending SIGKILL...")
                    self.process.kill()
                    self.process.wait(timeout=3)
            except Exception as exc:
                logger.warning("Error terminating rclone process: %s", exc)

        # 3. Final fallback unmount if still reported as mounted
        if is_path_mounted(self.mount_point):
            unmount_fuse_path(self.mount_point, lazy=True)

        self.process = None
        self.rc_credentials = None
        self.current_remote = None
        self._set_state("stopped")
        return unmounted

    def is_running(self) -> bool:
        """Checks if the mount process is actively running and path is mounted."""
        return self.process is not None and self.process.poll() is None
