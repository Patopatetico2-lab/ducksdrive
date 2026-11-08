"""
keyring_manager.py - Linux Secret Service / Keyring integration for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Integrates with the Linux Desktop Secret Service API (GNOME Keyring, KWallet, KeePassXC)
via `secret-tool` to securely store and retrieve the Rclone configuration encryption password,
allowing `--password-command` to decrypt `rclone.conf` without hardcoded master keys on disk.
"""

import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SERVICE = "ducksdrive"
DEFAULT_USER = "rclone"
DEFAULT_LABEL = "DucksDrive Rclone Configuration Password"


def is_keyring_available() -> bool:
    """Checks whether the `secret-tool` utility is available on this system."""
    return shutil.which("secret-tool") is not None


def get_password_command(service: str = DEFAULT_SERVICE, user: str = DEFAULT_USER) -> str:
    """Returns the shell command string to be passed to rclone's `--password-command` flag."""
    return f"secret-tool lookup service {service} user {user}"


def store_rclone_password(
    password: str,
    service: str = DEFAULT_SERVICE,
    user: str = DEFAULT_USER,
    label: str = DEFAULT_LABEL,
) -> bool:
    """
    Stores the configuration password in the system keyring via `secret-tool store`.

    Args:
        password: The master password to store.
        service: Secret service identifier attribute.
        user: Username attribute.
        label: Human-readable display label in the desktop keyring manager.

    Returns:
        bool: True if stored successfully, False otherwise.
    """
    tool = shutil.which("secret-tool")
    if not tool:
        logger.warning("secret-tool not found; cannot store password in system keyring.")
        return False

    cmd = [tool, "store", f"--label={label}", "service", service, "user", user]
    try:
        proc = subprocess.run(
            cmd,
            input=password,
            text=True,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0:
            logger.info("Successfully stored rclone password in system keyring.")
            return True
        logger.warning("Failed to store password in keyring (exit code %d): %s", proc.returncode, proc.stderr.strip())
    except Exception as exc:
        logger.error("Error executing secret-tool store: %s", exc)
    return False


def get_rclone_password(service: str = DEFAULT_SERVICE, user: str = DEFAULT_USER) -> Optional[str]:
    """
    Retrieves the configuration password from the system keyring via `secret-tool lookup`.

    Returns:
        Optional[str]: The decrypted password, or None if not found or on error.
    """
    tool = shutil.which("secret-tool")
    if not tool:
        return None

    cmd = [tool, "lookup", "service", service, "user", user]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except Exception as exc:
        logger.debug("Error looking up password in keyring: %s", exc)
    return None


def clear_rclone_password(service: str = DEFAULT_SERVICE, user: str = DEFAULT_USER) -> bool:
    """
    Clears the configuration password from the system keyring via `secret-tool clear`.

    Returns:
        bool: True if cleared successfully, False otherwise.
    """
    tool = shutil.which("secret-tool")
    if not tool:
        return False

    cmd = [tool, "clear", "service", service, "user", user]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception as exc:
        logger.error("Error clearing password from keyring: %s", exc)
    return False
