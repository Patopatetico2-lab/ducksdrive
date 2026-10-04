"""
rclone_config.py - Module for managing Rclone remote configurations.

Handles listing existing remotes and creating new OAuth-based cloud remotes
in a background thread to prevent blocking the Qt event loop.
"""

import json
import logging
import os
import select
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from i18n import tr
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# Commonly used cloud storage providers supported by Rclone
SUPPORTED_REMOTE_TYPES = [
    {"type": "drive", "label": "Google Drive"},
    {"type": "onedrive", "label": "Microsoft OneDrive"},
    {"type": "dropbox", "label": "Dropbox"},
    {"type": "mega", "label": "Mega"},
    {"type": "box", "label": "Box"},
    {"type": "pcloud", "label": "pCloud"},
    {"type": "s3", "label": "Amazon S3 / S3-Compatible"},
    {"type": "webdav", "label": "Nextcloud / WebDAV"},
    {"type": "ftp", "label": "FTP"},
    {"type": "sftp", "label": "SFTP"},
]


_SENSITIVE_PARAMS = {"pass", "password", "secret_access_key", "token", "client_secret"}


def mask_command(cmd: List[str]) -> str:
    """Returns the command line as a string with the value of sensitive `key value` pairs hidden."""
    masked: List[str] = []
    for i, arg in enumerate(cmd):
        if i > 0 and cmd[i - 1].lower() in _SENSITIVE_PARAMS:
            masked.append("******")
        else:
            masked.append(arg)
    return " ".join(masked)


def get_rclone_conf_path() -> str:
    """Returns the active rclone config path (RCLONE_CONFIG, `rclone config file`, or the default)."""
    env_path = os.environ.get("RCLONE_CONFIG")
    if env_path:
        return os.path.expanduser(env_path)
    try:
        res = subprocess.run([get_rclone_path(), "config", "file"], capture_output=True, text=True, timeout=5)
        lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        if res.returncode == 0 and lines:
            return lines[-1]
    except Exception:
        pass
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg, "rclone", "rclone.conf")


def get_rclone_path() -> str:
    """
    Locates the rclone executable in the system PATH.

    Returns:
        str: Absolute path to the rclone executable.

    Raises:
        FileNotFoundError: If rclone binary is not found.
    """
    path = shutil.which("rclone")
    if not path:
        raise FileNotFoundError("The 'rclone' executable was not found in system PATH.")
    return path


def list_remotes() -> Dict[str, Dict[str, Any]]:
    """
    Retrieves configured remotes using `rclone config dump`.

    Returns:
        Dict[str, Dict[str, Any]]: Dictionary mapping remote names to their configuration.
    """
    rclone_bin = get_rclone_path()
    try:
        result = subprocess.run(
            [rclone_bin, "config", "dump"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            return data
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as err:
        logger.warning("Failed to dump rclone config as JSON: %s. Falling back to listremotes.", err)

    # Fallback to `rclone listremotes`
    try:
        result = subprocess.run(
            [rclone_bin, "listremotes"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        remotes: Dict[str, Dict[str, Any]] = {}
        for line in result.stdout.splitlines():
            name = line.strip().rstrip(":")
            if name:
                remotes[name] = {"type": "unknown"}
        return remotes
    except Exception as exc:
        logger.error("Failed to list rclone remotes: %s", exc)
        return {}


def list_remote_names() -> List[str]:
    """
    Returns a sorted list of configured remote names (without trailing colons).

    Returns:
        List[str]: List of remote names.
    """
    remotes = list_remotes()
    return sorted(list(remotes.keys()))


class RemoteCreationThread(QThread):
    """
    Background worker thread to run `rclone config create` asynchronously,
    preventing the GUI from freezing during OAuth browser authorization.
    """
    started_creation = Signal(str)
    output_line = Signal(str)
    finished_creation = Signal(bool, str)

    def __init__(
        self,
        remote_name: str,
        remote_type: str,
        extra_params: Optional[Dict[str, str]] = None,
        timeout: int = 300,
        parent: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.remote_name = remote_name
        self.remote_type = remote_type
        self.extra_params = extra_params or {}
        self.timeout = timeout
        self._process: Optional[subprocess.Popen[str]] = None
        self._is_cancelled = False

    def run(self) -> None:
        """Executes the remote configuration command and streams log messages."""
        self.started_creation.emit(self.remote_name)
        try:
            rclone_bin = get_rclone_path()
        except FileNotFoundError as fnf:
            self.finished_creation.emit(False, str(fnf))
            return

        cmd = [rclone_bin, "config", "create", self.remote_name, self.remote_type]
        params = {"config_is_local": "true"}
        params.update(self.extra_params)
        for key, value in params.items():
            cmd.extend([key, str(value)])

        logger.info("Starting background creation: %s", mask_command(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            output_lines: List[str] = []
            timed_out = False
            if self._process.stdout:
                fd = self._process.stdout.fileno()
                os.set_blocking(fd, False)
                deadline = time.time() + self.timeout
                buffer = b""

                while True:
                    if self._is_cancelled:
                        break
                    if time.time() > deadline:
                        timed_out = True
                        self._process.kill()
                        break
                    
                    ready, _, _ = select.select([fd], [], [], 1.0)
                    if ready:
                        try:
                            chunk = os.read(fd, 4096)
                            if not chunk:  # EOF
                                if buffer.strip():
                                    decoded = buffer.decode("utf-8", errors="replace").strip()
                                    output_lines.append(decoded)
                                    self.output_line.emit(decoded)
                                break
                            buffer += chunk
                            while b"\n" in buffer:
                                line_bytes, buffer = buffer.split(b"\n", 1)
                                decoded = line_bytes.decode("utf-8", errors="replace").strip()
                                if decoded:
                                    output_lines.append(decoded)
                                    self.output_line.emit(decoded)
                        except BlockingIOError:
                            pass
                    elif self._process.poll() is not None:
                        break
                        
                self._process.stdout.close()

            self._process.wait(timeout=5)
            ret_code = self._process.returncode

            if timed_out:
                self.finished_creation.emit(False, f"Operation timed out after {self.timeout}s.")
                return

            if self._is_cancelled:
                self.finished_creation.emit(False, f"Creation of '{self.remote_name}' was cancelled.")
                return

            if ret_code == 0:
                self.finished_creation.emit(True, f"Remote '{self.remote_name}' configured successfully.")
            else:
                err_summary = "\n".join(output_lines[-5:]) if output_lines else "Unknown error"
                self.finished_creation.emit(
                    False,
                    f"Configuration failed (code {ret_code}):\n{err_summary}",
                )

        except subprocess.TimeoutExpired:
            self.cancel()
            self.finished_creation.emit(False, f"Operation timed out after {self.timeout}s.")
        except Exception as exc:
            self.finished_creation.emit(False, f"Exception while configuring remote: {str(exc)}")

    def cancel(self) -> None:
        """Cancels and terminates the active process if running."""
        self._is_cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass


def delete_remote(remote_name: str) -> bool:
    """
    Safely deletes an rclone remote by calling `rclone config delete <remote_name>`.

    Args:
        remote_name: Name of the remote to delete.

    Returns:
        bool: True if deleted successfully, False otherwise.
    """
    conf_path = get_rclone_conf_path()
    bak_path = conf_path + ".bak"
    # Keep the oldest backup: never overwrite it with an already-modified copy
    if os.path.exists(conf_path) and not os.path.exists(bak_path):
        try:
            shutil.copy2(conf_path, bak_path)
        except Exception as e:
            logger.warning("Failed to backup rclone.conf: %s", e)

    try:
        rclone_bin = get_rclone_path()
        clean_name = remote_name.rstrip(":")
        cmd = [rclone_bin, "config", "delete", clean_name]
        logger.info("Executing rclone config delete: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            logger.info("Remote '%s' deleted successfully.", clean_name)
            return True
        logger.error("Failed to delete remote '%s': %s", clean_name, res.stderr.strip() or res.stdout.strip())
        return False
    except Exception as exc:
        logger.error("Exception deleting remote '%s': %s", remote_name, exc)
        return False
