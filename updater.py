"""
updater.py - Update checker and version manager for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)

APP_VERSION = "1.1.0"
GITHUB_REPO = "Patopatetico2-lab/ducksdrive"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_WEB_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"

CACHE_TTL_SECONDS = 86400  # 24 hours between automatic checks to respect GitHub API rate limits


def parse_version(version_str: str) -> Tuple[int, ...]:
    """
    Parses a version string (e.g. 'v1.0.8', '1.0.9', 'v1.0.10-beta') into a comparable tuple of integers.
    """
    clean = version_str.lstrip("vV").strip()
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", clean)
    if match:
        return tuple(int(x or 0) for x in match.groups())
    nums = re.findall(r"\d+", clean)
    return tuple(int(x) for x in nums) if nums else (0, 0, 0)


def _get_cache_file() -> str:
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "ducksdrive", "update_cache.json")


def _read_cache() -> Optional[dict]:
    cache_path = _get_cache_file()
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("Failed to read update cache: %s", e)
    return None


def _write_cache(latest_version: str, release_url: str, release_notes: str) -> None:
    cache_path = _get_cache_file()
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": time.time(),
                    "latest_version": latest_version,
                    "release_url": release_url,
                    "release_notes": release_notes[:1000] if release_notes else "",
                },
                f,
                indent=2,
            )
    except Exception as e:
        logger.debug("Failed to write update cache: %s", e)


def check_for_updates(force: bool = False, timeout: float = 6.0) -> Tuple[bool, str, str, str]:
    """
    Checks GitHub Releases for a newer version of DucksDrive.

    Args:
        force: If True, bypasses the 24-hour cache and queries GitHub API directly.
        timeout: Network timeout in seconds.

    Returns:
        Tuple[bool, str, str, str]: (is_update_available, latest_version, release_url, release_notes)
    """
    if not force:
        cached = _read_cache()
        if cached:
            ts = cached.get("timestamp", 0)
            if time.time() - ts < CACHE_TTL_SECONDS:
                latest_ver = str(cached.get("latest_version") or APP_VERSION)
                url = str(cached.get("release_url") or RELEASES_WEB_URL)
                notes = str(cached.get("release_notes") or "")
                is_newer = parse_version(latest_ver) > parse_version(APP_VERSION)
                return is_newer, latest_ver, url, notes

    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "User-Agent": f"DucksDrive/{APP_VERSION} (Linux; +https://github.com/{GITHUB_REPO})",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                tag_name = str(payload.get("tag_name") or "").strip()
                html_url = str(payload.get("html_url") or RELEASES_WEB_URL)
                notes = str(payload.get("body") or "")

                if tag_name:
                    _write_cache(tag_name, html_url, notes)
                    is_newer = parse_version(tag_name) > parse_version(APP_VERSION)
                    return is_newer, tag_name, html_url, notes
    except urllib.error.HTTPError as http_err:
        logger.debug("HTTP error checking for updates: %s", http_err)
    except urllib.error.URLError as url_err:
        logger.debug("Network error checking for updates: %s", url_err)
    except Exception as exc:
        logger.debug("Unexpected error checking for updates: %s", exc)

    return False, APP_VERSION, RELEASES_WEB_URL, ""


class UpdateCheckWorker(QThread):
    """Background worker to check for updates without freezing the GUI."""

    # Emits: (is_update_available: bool, latest_version: str, release_url: str, release_notes: str)
    check_finished = Signal(bool, str, str, str)
    check_failed = Signal(str)
    update_available = Signal(str, str)

    def __init__(self, force: bool = False, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.force = force

    def run(self) -> None:
        try:
            has_update, latest_ver, url, notes = check_for_updates(force=self.force)
            self.check_finished.emit(has_update, latest_ver, url, notes)
            if has_update:
                self.update_available.emit(latest_ver, url)
        except Exception as exc:
            self.check_failed.emit(str(exc))
