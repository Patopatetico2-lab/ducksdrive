"""
ducksdrive/core/rclone_config.py - Module for managing Rclone remote configurations in DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Handles listing existing remotes and creating new OAuth-based cloud remotes
in a background thread to prevent blocking the Qt event loop.
"""

import gc
import json
import logging
import os
import select
import shutil
import subprocess
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ducksdrive.i18n import tr

logger = logging.getLogger(__name__)


class BackgroundTask(QThread):
    """Runs a single blocking callable off the GUI thread and emits itself when done."""

    completed = Signal(object)  # emits this task; read `.result` afterwards

    def __init__(self, func: Callable[..., Any], *args: Any) -> None:
        super().__init__()
        self._func = func
        self._args = args
        self.result: Any = None
        self.callback: Optional[Callable[[Any], None]] = None

    def run(self) -> None:
        try:
            self.result = self._func(*self._args)
        except Exception as exc:
            logger.error("Background task %s failed: %s", getattr(self._func, "__name__", self._func), exc)
            self.result = None
        self.completed.emit(self)


class TaskRunner(QObject):
    """
    Runs blocking callables one at a time (FIFO) in background threads and delivers each
    result to its callback on the thread that owns the runner (the GUI thread).

    Serial execution keeps ordering guarantees, e.g. "add bookmark" always finishes
    before a following "remove bookmark".
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._queue: Deque[Tuple[Callable[..., Any], Tuple[Any, ...], Optional[Callable[[Any], None]]]] = deque()
        self._current: Optional[BackgroundTask] = None

    def submit(
        self,
        func: Callable[..., Any],
        *args: Any,
        callback: Optional[Callable[[Any], None]] = None,
    ) -> None:
        """Queues `func(*args)`; `callback(result)` runs in the GUI thread (result is None on failure)."""
        self._queue.append((func, args, callback))
        self._start_next()

    def _start_next(self) -> None:
        if self._current is not None or not self._queue:
            return
        func, args, callback = self._queue.popleft()
        task = BackgroundTask(func, *args)
        task.callback = callback
        task.completed.connect(self._on_completed)
        self._current = task
        task.start()

    @Slot(object)
    def _on_completed(self, task: BackgroundTask) -> None:
        if task is not self._current:
            task.callback = None
            task.deleteLater()
            return  # already drained by wait_all()
        task.wait()  # the thread is only returning from run(); this is instantaneous
        self._current = None
        callback = task.callback
        task.callback = None
        task.deleteLater()
        if callback is not None:
            try:
                callback(task.result)
            except Exception:
                logger.exception("Background task callback failed")
        self._start_next()

    def wait_all(self, timeout_ms: int = 5000, run_queued: bool = True) -> None:
        """
        Waits for the active background task (and optionally queued tasks) to finish.
        Pass `run_queued=False` on shutdown when unstarted tasks (e.g. UI refreshes)
        should be discarded instead of spawned.
        """
        if not run_queued:
            self._queue.clear()
        gc.collect()
        deadline = time.monotonic() + timeout_ms / 1000.0
        while self._current is not None or (run_queued and self._queue):
            if self._current is None:
                self._start_next()
                continue
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or not self._current.wait(remaining):
                logger.warning("Timed out waiting for background tasks to finish.")
                return
            finished_task = self._current
            self._current = None
            finished_task.callback = None
            finished_task.deleteLater()

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


_SENSITIVE_PARAMS = {"pass", "password", "secret_access_key", "token", "client_secret", "--rc-pass", "--password-command"}


def mask_command(cmd: List[str]) -> str:
    """Returns the command line as a string with the value of sensitive `key value` or `key=value` pairs hidden."""
    masked: List[str] = []
    for i, arg in enumerate(cmd):
        if i > 0 and cmd[i - 1].lower() in _SENSITIVE_PARAMS:
            masked.append("******")
        elif "=" in arg and arg.split("=", 1)[0].lower() in _SENSITIVE_PARAMS:
            key = arg.split("=", 1)[0]
            masked.append(f"{key}=******")
        else:
            masked.append(arg)
    return " ".join(masked)



_REMOTE_NAMES_CACHE: Optional[Tuple[str, int, int, List[str]]] = None
_REMOTE_TYPES_CACHE: Dict[str, str] = {}


def _get_default_rclone_conf_path() -> str:
    """Returns the default, legacy (~/.rclone.conf), or RCLONE_CONFIG path without spawning a subprocess."""
    env_path = os.environ.get("RCLONE_CONFIG")
    if env_path:
        return os.path.expanduser(env_path)
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    primary = os.path.join(xdg, "rclone", "rclone.conf")
    if os.path.isfile(primary):
        return primary
    legacy = os.path.expanduser("~/.rclone.conf")
    if os.path.isfile(legacy):
        return legacy
    return primary


def get_clean_env(include_keyring: bool = True) -> Dict[str, str]:
    """
    Returns a clean environment for external subprocesses (browsers, file managers, rclone)
    stripped of AppImage/PyInstaller/bundled Qt library overrides.
    """
    env = os.environ.copy()
    for var in (
        "LD_LIBRARY_PATH",
        "APPDIR",
        "QT_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "PYTHONHOME",
    ):
        env.pop(var, None)
    if include_keyring:
        try:
            from ducksdrive.system.keyring_manager import get_password_command, is_keyring_available
            from ducksdrive.core.rclone_settings import load_config

            if load_config().get("use_system_keyring") and is_keyring_available():
                env["RCLONE_PASSWORD_COMMAND"] = get_password_command()
        except Exception:
            pass
    return env


def get_rclone_conf_path() -> str:
    """Returns the active rclone config path (RCLONE_CONFIG, `rclone config file`, or the default)."""
    env_path = os.environ.get("RCLONE_CONFIG")
    if env_path:
        return os.path.expanduser(env_path)
    try:
        res = subprocess.run(
            [get_rclone_path(), "config", "file"],
            capture_output=True,
            text=True,
            timeout=5,
            env=get_clean_env(include_keyring=False),
        )
        lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        if res.returncode == 0 and lines:
            return lines[-1]
    except Exception:
        pass
    return _get_default_rclone_conf_path()


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
            env=get_clean_env()
        )
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            for rem_name, rem_data in data.items():
                if isinstance(rem_data, dict) and rem_data.get("type"):
                    _REMOTE_TYPES_CACHE[rem_name.rstrip(":")] = str(rem_data["type"]).strip().lower()
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
            env=get_clean_env()
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


def _list_remote_names_from_conf_fast() -> Optional[List[str]]:
    """
    Reads remote names directly from an unencrypted rclone.conf using an mtime+size cache.
    Returns None if the config file is missing, encrypted, or has no sections.
    """
    global _REMOTE_NAMES_CACHE
    conf_path = _get_default_rclone_conf_path()
    try:
        st = os.stat(conf_path)
        mtime_ns, size = st.st_mtime_ns, st.st_size
    except OSError:
        return None

    if (
        _REMOTE_NAMES_CACHE is not None
        and _REMOTE_NAMES_CACHE[0] == conf_path
        and _REMOTE_NAMES_CACHE[1] == mtime_ns
        and _REMOTE_NAMES_CACHE[2] == size
    ):
        return list(_REMOTE_NAMES_CACHE[3])

    names: List[str] = []
    seen_section = False
    current_section: Optional[str] = None
    try:
        with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    if "RCLONE ENCRYPTED" in line.upper():
                        return None
                    continue
                if "RCLONE_ENCRYPT" in line or "RCLONE ENCRYPTED" in line.upper():
                    return None
                if line.startswith("[") and line.endswith("]") and len(line) > 2:
                    seen_section = True
                    section_name = line[1:-1].strip()
                    current_section = section_name or None
                    if section_name:
                        names.append(section_name)
                elif not seen_section:
                    return None
                elif current_section and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == "type":
                        _REMOTE_TYPES_CACHE[current_section] = v.strip().lower()
    except OSError:
        return None

    if not seen_section:
        return None

    result = sorted(set(names))
    _REMOTE_NAMES_CACHE = (conf_path, mtime_ns, size, result)
    return list(result)


def list_remote_names() -> List[str]:
    """
    Returns a sorted list of configured remote names (without trailing colons).

    Returns:
        List[str]: List of remote names.
    """
    fast_names = _list_remote_names_from_conf_fast()
    if fast_names is not None:
        return fast_names
    remotes = list_remotes()
    return sorted(list(remotes.keys()))


def get_remote_type_fast(remote_name: str) -> Optional[str]:
    """
    Reads the remote backend `type` directly from rclone.conf without spawning a subprocess,
    falling back to the in-memory type cache populated by `list_remotes()` (works with encrypted configs).
    Safe to call on the GUI thread.
    """
    clean_name = remote_name.rstrip(":")
    conf_path = _get_default_rclone_conf_path()

    if os.path.isfile(conf_path):
        in_section = False
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or line.startswith(";"):
                        continue
                    if "RCLONE_ENCRYPT" in line or "RCLONE ENCRYPTED" in line.upper():
                        break
                    if line.startswith("[") and line.endswith("]"):
                        in_section = (line[1:-1].strip() == clean_name)
                        continue
                    if in_section and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip().lower() == "type":
                            rtype = v.strip().lower()
                            _REMOTE_TYPES_CACHE[clean_name] = rtype
                            return rtype
        except OSError:
            pass
    return _REMOTE_TYPES_CACHE.get(clean_name)


def has_custom_drive_client_id(remote_name: str) -> bool:
    """Returns True if the given Google Drive remote configures its own OAuth `client_id` in rclone.conf."""
    cid, _ = get_drive_client_credentials(remote_name)
    return bool(cid)


def get_drive_client_credentials(remote_name: str) -> Tuple[str, str]:
    """
    Reads (client_id, client_secret) for the given remote directly from an unencrypted rclone.conf.
    Safe to call on the GUI thread. Returns ("", "") if not present or encrypted.
    """
    clean_name = remote_name.rstrip(":")
    conf_path = _get_default_rclone_conf_path()
    if not os.path.isfile(conf_path):
        return "", ""
    in_section = False
    client_id = ""
    client_secret = ""
    try:
        with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "RCLONE_ENCRYPT" in line or "RCLONE ENCRYPTED" in line.upper():
                    return "", ""
                if line.startswith("[") and line.endswith("]"):
                    in_section = (line[1:-1].strip() == clean_name)
                    continue
                if in_section and "=" in line:
                    k, v = line.split("=", 1)
                    key = k.strip().lower()
                    if key == "client_id":
                        client_id = v.strip()
                    elif key == "client_secret":
                        client_secret = v.strip()
    except OSError:
        return "", ""
    return client_id, client_secret


def update_drive_client_credentials(remote_name: str, client_id: str, client_secret: str) -> Tuple[bool, str]:
    """
    Updates `client_id` and `client_secret` on an existing Google Drive remote via
    `rclone config update --non-interactive` without triggering an interactive OAuth browser flow.
    """
    global _REMOTE_NAMES_CACHE
    clean_name = remote_name.rstrip(":")
    if not clean_name:
        return False, tr("O nome da nuvem não pode estar vazio.")
    try:
        rclone_bin = get_rclone_path()
    except FileNotFoundError as exc:
        return False, tr(str(exc))

    cmd = [
        rclone_bin,
        "config",
        "update",
        "--non-interactive",
        "--",
        clean_name,
        "client_id",
        client_id.strip(),
        "client_secret",
        client_secret.strip(),
    ]
    logger.info("Updating Drive OAuth client credentials: %s", mask_command(cmd))
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env=get_clean_env(),
        )
        _REMOTE_NAMES_CACHE = None
        if res.returncode == 0:
            return True, tr("Credenciais OAuth atualizadas para '%s'.") % clean_name
        err = (res.stderr or res.stdout or "").strip() or (tr("Código de erro: %d") % res.returncode)
        return False, err
    except Exception as exc:
        return False, str(exc)


_VFS_CHUNK_STREAMS_SUPPORT_CACHE: Optional[bool] = None


def supports_vfs_read_chunk_streams(rclone_bin: Optional[str] = None) -> bool:
    """
    Returns True if the installed rclone supports `--vfs-read-chunk-streams` (rclone >= 1.66.0).
    Result is cached in memory so `rclone version` is invoked at most once per session.
    """
    global _VFS_CHUNK_STREAMS_SUPPORT_CACHE
    if _VFS_CHUNK_STREAMS_SUPPORT_CACHE is not None:
        return _VFS_CHUNK_STREAMS_SUPPORT_CACHE
    try:
        binary = rclone_bin or get_rclone_path()
        res = subprocess.run(
            [binary, "version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
            env=get_clean_env(include_keyring=False),
        )
        first_line = (res.stdout.splitlines() or [""])[0].strip().lower()
        # Format: "rclone v1.76.0"
        for token in first_line.split():
            ver = token.lstrip("v")
            parts = ver.split("-")[0].split(".")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                major, minor = int(parts[0]), int(parts[1])
                _VFS_CHUNK_STREAMS_SUPPORT_CACHE = (major, minor) >= (1, 66)
                return _VFS_CHUNK_STREAMS_SUPPORT_CACHE
    except Exception:
        pass
    _VFS_CHUNK_STREAMS_SUPPORT_CACHE = False
    return False


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
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._is_cancelled = False

    @staticmethod
    def _obscure_password_via_stdin(rclone_bin: str, password: str) -> Optional[str]:
        """Obscures a password through stdin so plaintext never appears in process argv."""
        try:
            res = subprocess.run(
                [rclone_bin, "obscure", "-"],
                input=password,
                text=True,
                capture_output=True,
                timeout=5,
                env=get_clean_env(include_keyring=False),
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as exc:
            logger.debug("Failed to pre-obscure password via stdin: %s", exc)
        return None

    @property
    def is_cancelled(self) -> bool:
        """True if the user cancelled the remote creation."""
        return self._is_cancelled

    def run(self) -> None:
        """Executes the remote configuration command and streams log messages."""
        self.started_creation.emit(self.remote_name)
        try:
            rclone_bin = get_rclone_path()
        except FileNotFoundError as fnf:
            self.finished_creation.emit(False, tr(str(fnf)))
            return

        params: Dict[str, str] = {}
        if self.remote_type in ("drive", "onedrive", "dropbox", "box", "pcloud"):
            params["config_is_local"] = "true"
        params.update(self.extra_params)

        cmd = [rclone_bin, "config", "create"]
        if "pass" in params and self.remote_type in ("mega", "ftp", "sftp", "webdav"):
            obscured = self._obscure_password_via_stdin(rclone_bin, str(params["pass"]))
            if obscured is not None:
                params["pass"] = obscured
                cmd.append("--no-obscure")
            else:
                cmd.append("--obscure")
        cmd.extend(["--", self.remote_name, self.remote_type])
        for key, value in params.items():
            cmd.extend([key, str(value)])

        logger.info("Starting background creation: %s", mask_command(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env=get_clean_env()
            )

            output_lines: List[str] = []
            timed_out = False
            if self._process.stdout:
                fd = self._process.stdout.fileno()
                os.set_blocking(fd, False)
                deadline = time.time() + self.timeout
                buffer = b""

                def _drain_buffer(flush_rest: bool = False) -> None:
                    nonlocal buffer
                    while b"\n" in buffer:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                        decoded = line_bytes.decode("utf-8", errors="replace").strip()
                        if decoded:
                            output_lines.append(decoded)
                            self.output_line.emit(decoded)
                    if flush_rest and buffer.strip():
                        decoded = buffer.decode("utf-8", errors="replace").strip()
                        output_lines.append(decoded)
                        self.output_line.emit(decoded)
                        buffer = b""

                while True:
                    if self._is_cancelled:
                        if self._process.poll() is None:
                            try:
                                self._process.terminate()
                            except Exception:
                                pass
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
                                _drain_buffer(flush_rest=True)
                                break
                            buffer += chunk
                            _drain_buffer(flush_rest=False)
                        except BlockingIOError:
                            pass
                    elif self._process.poll() is not None:
                        # Drain any remaining bytes written just before process exit
                        while True:
                            try:
                                tail = os.read(fd, 4096)
                                if not tail:
                                    break
                                buffer += tail
                            except (BlockingIOError, OSError):
                                break
                        _drain_buffer(flush_rest=True)
                        break

                self._process.stdout.close()

            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except Exception:
                    pass
            ret_code = self._process.returncode

            if timed_out:
                self.finished_creation.emit(False, tr("A operação excedeu o tempo limite de %ds.") % self.timeout)
                return

            if self._is_cancelled:
                self.finished_creation.emit(False, tr("A criação de '%s' foi cancelada.") % self.remote_name)
                return

            if ret_code == 0:
                self.finished_creation.emit(True, tr("Nuvem '%s' configurada com sucesso.") % self.remote_name)
            else:
                err_summary = "\n".join(output_lines[-5:]) if output_lines else tr("Erro desconhecido")
                self.finished_creation.emit(
                    False,
                    tr("Falha na configuração (código %s):\n%s") % (ret_code, err_summary),
                )

        except subprocess.TimeoutExpired:
            self.cancel()
            self.finished_creation.emit(False, tr("A operação excedeu o tempo limite de %ds.") % self.timeout)
        except Exception as exc:
            self.finished_creation.emit(False, tr("Erro inesperado ao configurar nuvem: %s") % str(exc))

    def cancel(self) -> None:
        """Cancels and terminates the active process if running."""
        self._is_cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
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
        cmd = [rclone_bin, "config", "delete", "--", clean_name]
        logger.info("Executing rclone config delete: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=get_clean_env())
        if res.returncode == 0:
            global _REMOTE_NAMES_CACHE
            _REMOTE_NAMES_CACHE = None
            _REMOTE_TYPES_CACHE.pop(clean_name, None)
            logger.info("Remote '%s' deleted successfully.", clean_name)
            return True
        logger.error("Failed to delete remote '%s': %s", clean_name, res.stderr.strip() or res.stdout.strip())
        return False
    except Exception as exc:
        logger.error("Exception deleting remote '%s': %s", remote_name, exc)
        return False


def check_remote_connection(remote_name: str) -> Tuple[bool, str, bool]:
    """
    Executes 'rclone about <remote>: --json --timeout 5s --contimeout 5s --retries 1 --low-level-retries 1'
    with stdin=subprocess.DEVNULL to test remote connectivity and OAuth health.

    Returns:
        Tuple[is_healthy: bool, message: str, is_oauth_expired: bool]
    """
    rclone_bin = get_rclone_path()
    clean_name = remote_name.rstrip(":")
    formatted = f"{clean_name}:"
    cmd = [
        rclone_bin,
        "about",
        formatted,
        "--json",
        "--timeout", "5s",
        "--contimeout", "5s",
        "--retries", "1",
        "--low-level-retries", "1",
    ]
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=8,
            env=get_clean_env(),
        )
        if res.returncode == 0:
            return True, tr("A conexão com '%s' está saudável e autenticada com sucesso.") % clean_name, False

        err = (res.stderr or "").lower()
        is_oauth = any(x in err for x in ("invalid_grant", "token expired", "token has expired", "token revoked", "oauth"))
        if is_oauth:
            msg = tr("O token de autenticação OAuth para '%s' está expirado ou revogado (invalid_grant).") % clean_name
            return False, msg, True
        else:
            clean_err = res.stderr.strip() if res.stderr else tr("Código de erro: %d") % res.returncode
            return False, tr("Falha na conexão com '%s':\n%s") % (clean_name, clean_err), False
    except subprocess.TimeoutExpired:
        return False, tr("Tempo limite esgotado (timeout de 5s) ao conectar com '%s'. Verifique a sua conexão com a internet.") % clean_name, False
    except Exception as exc:
        return False, tr("Erro ao testar conexão com '%s': %s") % (clean_name, exc), False


def reconnect_remote(remote_name: str) -> Tuple[int, str]:
    """
    Reauthorizes an expired OAuth remote via non-blocking 'rclone config reconnect <remote>:'.
    Answers 'y\\ny\\n\\n' automatically into stdin to refresh token and open web browser.

    Returns:
        Tuple[returncode: int, output_or_error: str]
    """
    rclone_bin = get_rclone_path()
    clean_name = remote_name.rstrip(":")
    formatted = f"{clean_name}:"
    cmd = [rclone_bin, "config", "reconnect", formatted]
    try:
        res = subprocess.run(
            cmd,
            input="y\ny\n\n",
            text=True,
            capture_output=True,
            timeout=120,
            env=get_clean_env(),
        )
        return res.returncode, res.stderr or res.stdout or ""
    except Exception as exc:
        return -1, str(exc)
