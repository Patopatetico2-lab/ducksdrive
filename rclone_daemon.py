"""
rclone_daemon.py - Virtual drive lifecycle and FUSE mount daemon.

Manages the background `rclone mount` subprocess with VFS cache controls,
random in-memory Remote Control (RC) credentials, and robust FUSE unmounting
with dynamic fallback to fusermount3 or fusermount.
"""

import glob
import logging
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from PySide6.QtCore import QObject, Signal, QTimer, QCoreApplication

logger = logging.getLogger(__name__)

DEFAULT_MOUNT_POINT = os.path.expanduser("~/CloudDrives")
DEFAULT_RC_HOST = "127.0.0.1"
DEFAULT_RC_PORT = 5572
xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
LOG_DIR = os.path.join(xdg_data, "ducksdrive")


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



def get_clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("APPDIR", None)
    return env

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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=get_clean_env())
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
            lazy_res = subprocess.run([unmount_bin, "-uz", mount_path], capture_output=True, text=True, timeout=5, env=get_clean_env())
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
    if os.path.ismount(path):
        return True

    # Check /proc/mounts as fallback for FUSE mounts
    if os.path.exists("/proc/mounts"):
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mount_point_in_proc = unescape_mount_path(parts[1])
                        if mount_point_in_proc == path:
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
        self._startup_timer: Optional[QTimer] = None
        self._log_file: Optional[Any] = None

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

    def _check_process_health(self) -> None:
        """Actively monitors if the rclone mount process died unexpectedly in background."""
        if self.state == "mounted" and self.process is not None:
            if self.process.poll() is not None:
                logger.error("Rclone mount process died unexpectedly in background!")
                # Update state BEFORE emitting: the error slot opens a modal dialog whose nested
                # event loop would otherwise keep re-triggering this health check.
                self.stop()
                self._set_state("error")
                self.mount_error.emit("Rclone mount process died unexpectedly.")

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
            logger.info("Another mount is active. Stopping current mount before starting '%s'", remote_name)
            self.stop()

        rclone_bin = shutil.which("rclone")
        if not rclone_bin:
            err = "Rclone executable not found in PATH."
            logger.error(err)
            self.mount_error.emit(err)
            self._set_state("error")
            return False

        # Set dynamic mount point based on remote: ~/CloudDrives/{remote_name}
        clean_remote_name = remote_name.rstrip(":")
        self.mount_point = os.path.abspath(os.path.expanduser(f"~/CloudDrives/{clean_remote_name}"))

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

        # Load dynamic configuration preferences
        from rclone_settings import load_config
        cfg = load_config()
        bwlimit = str(cfg.get("bwlimit", "off"))
        cache_size = str(cfg.get("vfs_cache_max_size", "2G"))
        transfers = str(cfg.get("transfers", 4))
        vfs_cache_mode = str(cfg.get("vfs_cache_mode", "full"))

        # Build mandatory rclone mount arguments with user settings
        cmd = [
            rclone_bin,
            "mount",
            formatted_remote,
            self.mount_point,
            "--vfs-cache-mode",
            vfs_cache_mode,
            "--vfs-cache-max-size",
            cache_size,
            "--vfs-read-chunk-size",
            "64M",
            "--vfs-read-chunk-size-limit",
            "off",
        ]
        if bwlimit.lower() != "off":
            cmd.extend(["--bwlimit", bwlimit])
        cmd.extend([
            "--transfers",
            transfers,
            "--rc",
            "--rc-addr",
            f"{self.rc_credentials.host}:{self.rc_credentials.port}",
        ])

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
            err = f"Failed to open rclone log file '{log_path}': {exc}"
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
            err = f"Failed to spawn rclone mount: {exc}"
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

        def check_startup() -> None:
            if not self.process or self.state != "starting":
                startup_timer.stop()
                return

            if self.process.poll() is not None:
                startup_timer.stop()
                log_content = ""
                if self._log_file:
                    try:
                        self._log_file.flush()
                        with open(log_path, "r", encoding="utf-8") as lf:
                            log_content = lf.read()
                    except Exception:
                        pass
                err = f"Rclone mount exited during startup:\n{log_content.strip()}"
                logger.error(err)
                self.stop()
                self._set_state("error")
                self.mount_error.emit(err)
                return

            if is_path_mounted(self.mount_point):
                startup_timer.stop()
                self._set_state("mounted")
                return

            if time.time() - self._startup_start_time > 10.0:
                startup_timer.stop()
                err = f"Rclone mount timed out after 10s for path '{self.mount_point}'."
                logger.error(err)
                self.stop()
                self._set_state("error")
                self.mount_error.emit(err)
                return

        startup_timer.timeout.connect(check_startup)
        startup_timer.start()
        return True

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

        # Close dedicated log file
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        # 3. Final fallback unmount if still reported as mounted
        if is_path_mounted(self.mount_point):
            unmount_fuse_path(self.mount_point, lazy=True)

        if hasattr(self, "_startup_timer") and self._startup_timer:
            self._startup_timer.stop()
            self._startup_timer = None

        self.process = None
        self.rc_credentials = None
        self.current_remote = None
        self._set_state("stopped")
        return unmounted

    def is_running(self) -> bool:
        """Checks if the mount process is actively running and path is mounted."""
        return self.process is not None and self.process.poll() is None
