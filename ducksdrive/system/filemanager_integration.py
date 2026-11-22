"""
ducksdrive/system/filemanager_integration.py - Desktop integration for file manager sidebars in DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Provides functions to inject and remove the virtual drive path from:
1. KDE (Dolphin) sidebars via `~/.config/user-places.xbel` (XML) with a one-time .bak backup.
2. GTK (GNOME/XFCE/Nautilus/Thunar) sidebars via `~/.config/gtk-3.0/bookmarks`.

All writes are atomic (temporary file + os.replace).
"""

import hashlib
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Optional

logger = logging.getLogger(__name__)

# KDE's user-places.xbel has NO default namespace: <xbel>, <bookmark>, <title>, <info> and <metadata>
# are plain tags. Only prefixed tags use namespaces. The prefixes below MUST be registered, otherwise
# ElementTree rewrites existing entries (Home, Trash, ...) as ns0:/ns1: and Dolphin stops reading them.
NS_KDEPRIV = "http://www.kde.org/kdepriv"
NS_BOOKMARK = "http://www.freedesktop.org/standards/desktop-bookmarks"
NS_MIME = "http://www.freedesktop.org/standards/shared-mime-info"

ET.register_namespace("kdepriv", NS_KDEPRIV)
ET.register_namespace("bookmark", NS_BOOKMARK)
ET.register_namespace("mime", NS_MIME)

APP_NAME = "DucksDrive"


def _get_gtk_bookmarks_path() -> str:
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "gtk-3.0", "bookmarks")


GTK_BOOKMARKS_PATH = _get_gtk_bookmarks_path()


def _get_kde_places_path() -> str:
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    primary = os.path.join(xdg_data, "user-places.xbel")
    fallback = os.path.expanduser("~/.config/user-places.xbel")
    if os.path.exists(primary) or not os.path.exists(fallback):
        return primary
    return fallback


KDE_PLACES_PATH = _get_kde_places_path()


def _backup_once(path: str) -> None:
    """Creates `<path>.bak` only if it does not exist yet, so the pristine original is never overwritten."""
    bak = f"{path}.bak"
    if os.path.exists(path) and not os.path.exists(bak):
        try:
            shutil.copy2(path, bak)
        except Exception as e:
            logger.warning("Failed to create backup of %s: %s", path, e)


def _replace_atomically(tmp_path: str, target: str) -> None:
    """Moves tmp_path over target, keeping the original permissions."""
    if os.path.exists(target):
        try:
            shutil.copymode(target, tmp_path)
        except OSError:
            pass
    os.replace(tmp_path, target)


def _register_existing_namespaces(path: str) -> Optional[str]:
    """
    Registers every `xmlns:prefix="uri"` declared in `path` so ElementTree never rewrites
    KDE/Dolphin prefixes into `ns0:`, and returns any `<!DOCTYPE ...>` declaration to preserve.
    """
    doctype: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
        m_doc = re.search(r"<!DOCTYPE\s+[^>]+>", head)
        if m_doc:
            doctype = m_doc.group(0)
        for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"', head):
            ET.register_namespace(prefix, uri)
    except OSError:
        pass
    return doctype


def _atomic_write_text(path: str, content: str) -> None:
    target = os.path.realpath(path)  # follow symlinks (e.g. dotfile managers)
    parent_dir = os.path.dirname(target) or "."
    os.makedirs(parent_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ducksdrive-", suffix=".tmp", dir=parent_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        _replace_atomically(tmp_path, target)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _atomic_write_tree(tree: "ET.ElementTree[Any]", path: str, doctype: Optional[str] = None) -> None:
    target = os.path.realpath(path)
    parent_dir = os.path.dirname(target) or "."
    os.makedirs(parent_dir, exist_ok=True)
    if hasattr(ET, "indent"):
        ET.indent(tree, space=" ")  # keep the file human-readable like KDE writes it
    root = tree.getroot()
    if root is None:
        return
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_text = xml_bytes.decode("utf-8")
    if doctype and "<!DOCTYPE" not in xml_text:
        lines = xml_text.splitlines(keepends=True)
        if lines and lines[0].startswith("<?xml"):
            lines.insert(1, doctype + "\n")
            xml_text = "".join(lines)
        else:
            xml_text = doctype + "\n" + xml_text
    if not xml_text.endswith("\n"):
        xml_text += "\n"
    _atomic_write_text(target, xml_text)


def add_gtk_bookmark(path: str, label: str = APP_NAME) -> bool:
    """
    Adds a URI entry to GTK 3.0 bookmarks file.
    Format: file:///home/user/Path Label
    """
    bookmarks_path = _get_gtk_bookmarks_path()
    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"
    entry = f"{uri} {label}"

    os.makedirs(os.path.dirname(bookmarks_path), exist_ok=True)

    lines = []
    if os.path.exists(bookmarks_path):
        with open(bookmarks_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

    if any(line == uri or line.startswith(uri + " ") for line in lines):
        logger.info("GTK bookmark for %s already exists.", path)
        return True

    lines.append(entry)
    try:
        _atomic_write_text(bookmarks_path, "\n".join(lines) + "\n")
        logger.info("Added GTK bookmark: %s", entry)
        return True
    except Exception as e:
        logger.error("Failed to write GTK bookmarks: %s", e)
        return False


def remove_gtk_bookmark(path: str) -> bool:
    """Removes the exact URI entry (with or without label) from GTK bookmarks."""
    bookmarks_path = _get_gtk_bookmarks_path()
    if not os.path.exists(bookmarks_path):
        return True

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        with open(bookmarks_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = [line for line in lines if not (line.strip() == uri or line.strip().startswith(uri + " "))]

        if len(lines) == len(new_lines):
            return True

        _atomic_write_text(bookmarks_path, "".join(new_lines))
        logger.info("Removed GTK bookmark for %s", path)
        return True
    except Exception as e:
        logger.error("Failed to remove GTK bookmark: %s", e)
        return False


def add_kde_place(path: str, label: str = APP_NAME) -> bool:
    """
    Adds an entry to KDE's user-places.xbel (one-time .bak backup, atomic write).
    The structure mirrors the entries Dolphin itself writes:
      <bookmark href><title/><info>
        <metadata owner="http://freedesktop.org"><bookmark:icon name=.../></metadata>
        <metadata owner="http://www.kde.org"><ID/><isSystemItem/></metadata>
      </info></bookmark>
    """
    kde_places_path = _get_kde_places_path()
    if not os.path.exists(kde_places_path):
        logger.debug("KDE places file not found at %s", kde_places_path)
        return False

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        doctype = _register_existing_namespaces(kde_places_path)
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(kde_places_path, parser=parser)
        root = tree.getroot()

        for bookmark in root.findall("bookmark"):
            if bookmark.get("href") == uri:
                logger.info("KDE place for %s already exists.", path)
                return True

        _backup_once(kde_places_path)

        new_bookmark = ET.SubElement(root, "bookmark", {"href": uri})
        title = ET.SubElement(new_bookmark, "title")
        title.text = label

        info = ET.SubElement(new_bookmark, "info")

        meta_fd = ET.SubElement(info, "metadata", {"owner": "http://freedesktop.org"})
        ET.SubElement(meta_fd, f"{{{NS_BOOKMARK}}}icon", {"name": "folder-remote"})

        meta_kde = ET.SubElement(info, "metadata", {"owner": "http://www.kde.org"})
        ET.SubElement(meta_kde, "ID").text = hashlib.sha1(path.encode("utf-8")).hexdigest()
        ET.SubElement(meta_kde, "isSystemItem").text = "false"

        _atomic_write_tree(tree, kde_places_path, doctype=doctype)
        logger.info("Added KDE place: %s", label)
        return True
    except Exception as e:
        logger.error("Failed to update KDE places: %s", e)
        return False


def remove_kde_place(path: str) -> bool:
    """Removes the bookmark entry for the given path from user-places.xbel."""
    kde_places_path = _get_kde_places_path()
    if not os.path.exists(kde_places_path):
        return True

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        doctype = _register_existing_namespaces(kde_places_path)
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(kde_places_path, parser=parser)
        root = tree.getroot()

        to_remove = [b for b in root.findall("bookmark") if b.get("href") == uri]
        if not to_remove:
            return True

        _backup_once(kde_places_path)

        for bookmark in to_remove:
            root.remove(bookmark)

        _atomic_write_tree(tree, kde_places_path, doctype=doctype)
        logger.info("Removed KDE place for %s", path)
        return True
    except Exception as e:
        logger.error("Failed to remove KDE place: %s", e)
        return False


def open_local_folder(path: str) -> bool:
    """
    Opens `path` in the system file manager (Dolphin, Nautilus, Thunar, etc.) without
    blocking the GUI thread on FUSE `stat()` and with a clean environment (safe in AppImage/venv).
    """
    from ducksdrive.core.rclone_config import get_clean_env

    is_flatpak = os.path.exists("/.flatpak-info") or bool(os.environ.get("FLATPAK_ID"))
    if not is_flatpak:
        opener = shutil.which("xdg-open")
        if opener:
            try:
                from PySide6.QtCore import QProcess, QProcessEnvironment

                clean_env = get_clean_env(include_keyring=False)
                qenv = QProcessEnvironment()
                for k, v in clean_env.items():
                    qenv.insert(k, v)
                proc = QProcess()
                proc.setProgram(opener)
                proc.setArguments([path])
                proc.setProcessEnvironment(qenv)
                started, _pid = proc.startDetached()
                if started:
                    return True
            except Exception as exc:
                logger.debug("QProcess.startDetached failed for %s: %s", opener, exc)

    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
    except Exception as exc:
        logger.error("Failed to open folder '%s': %s", path, exc)
        return False


def integrate_mount(path: str, label: str = APP_NAME) -> None:
    """Helper to apply integration for all supported environments."""
    add_gtk_bookmark(path, label)
    add_kde_place(path, label)


def clean_integration(path: str) -> None:
    """Helper to remove integration from all supported environments."""
    remove_gtk_bookmark(path)
    remove_kde_place(path)
