"""
rclone_config.py - Module for managing Rclone remote configurations.

Handles listing existing remotes and creating new OAuth-based cloud remotes
in a background thread to prevent blocking the Qt event loop.
"""

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Fallback-safe PySide6 imports
try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover
    QThread = object  # type: ignore

    def Signal(*args: Any) -> Any:  # type: ignore
        """Dummy signal placeholder if PySide6 is not yet installed."""
        class _Signal:
            def emit(self, *a: Any, **kw: Any) -> None:
                pass

            def connect(self, slot: Any) -> None:
                pass
        return _Signal()


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
    {"type": "ftp", "label": "FTP / SFTP"},
]


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

        logger.info("Starting background creation: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            output_lines: List[str] = []
            if self._process.stdout:
                for line in iter(self._process.stdout.readline, ""):
                    if self._is_cancelled:
                        break
                    stripped = line.strip()
                    if stripped:
                        output_lines.append(stripped)
                        self.output_line.emit(stripped)
                self._process.stdout.close()

            self._process.wait(timeout=self.timeout)
            ret_code = self._process.returncode

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
