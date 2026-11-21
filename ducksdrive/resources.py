"""
resources.py - Centralized package path and asset resolution for DucksDrive.
"""

import os
import sys
from typing import Optional


def get_package_dir() -> str:
    """Returns the absolute path to the `ducksdrive` package directory."""
    return os.path.dirname(os.path.abspath(__file__))


def get_install_root() -> str:
    """Returns the installation or checkout root containing the `ducksdrive` package."""
    return os.path.dirname(get_package_dir())


def get_icon_path() -> Optional[str]:
    """Resolves the DucksDrive SVG icon path across PyInstaller, checkout, user, and system installs."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "ducksdrive", "assets", "icon.svg"))
        candidates.append(os.path.join(meipass, "icon.svg"))

    pkg_dir = get_package_dir()
    install_root = get_install_root()
    candidates.extend(
        [
            os.path.join(pkg_dir, "assets", "icon.svg"),
            os.path.join(install_root, "icon.svg"),
            os.path.expanduser("~/.local/share/ducksdrive/icon.svg"),
            "/usr/share/ducksdrive/icon.svg",
            "/usr/share/icons/hicolor/scalable/apps/ducksdrive.svg",
        ]
    )

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None
