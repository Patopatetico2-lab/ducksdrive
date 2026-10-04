"""
filemanager_integration.py - Desktop integration for file manager sidebars.

Provides functions to inject and remove the virtual drive path from:
1. KDE (Dolphin) sidebars via `~/.config/user-places.xbel` (XML) with a one-time .bak backup.
2. GTK (GNOME/XFCE/Nautilus/Thunar) sidebars via `~/.config/gtk-3.0/bookmarks`.

All writes are atomic (temporary file + os.replace).
"""

import hashlib
import logging
import os
import shutil
import urllib.parse
import xml.etree.ElementTree as ET

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
GTK_BOOKMARKS_PATH = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
KDE_PLACES_PATH = os.path.expanduser("~/.config/user-places.xbel")


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


def _atomic_write_text(path: str, content: str) -> None:
    target = os.path.realpath(path)  # follow symlinks (e.g. dotfile managers)
    tmp_path = f"{target}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        _replace_atomically(tmp_path, target)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _atomic_write_tree(tree: ET.ElementTree, path: str) -> None:
    target = os.path.realpath(path)
    tmp_path = f"{target}.tmp"
    try:
        if hasattr(ET, "indent"):
            ET.indent(tree, space=" ")  # keep the file human-readable like KDE writes it
        tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
        _replace_atomically(tmp_path, target)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def add_gtk_bookmark(path: str, label: str = APP_NAME) -> bool:
    """
    Adds a URI entry to GTK 3.0 bookmarks file.
    Format: file:///home/user/Path Label
    """
    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"
    entry = f"{uri} {label}"

    os.makedirs(os.path.dirname(GTK_BOOKMARKS_PATH), exist_ok=True)

    lines = []
    if os.path.exists(GTK_BOOKMARKS_PATH):
        with open(GTK_BOOKMARKS_PATH, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

    if any(line == uri or line.startswith(uri + " ") for line in lines):
        logger.info("GTK bookmark for %s already exists.", path)
        return True

    lines.append(entry)
    try:
        _atomic_write_text(GTK_BOOKMARKS_PATH, "\n".join(lines) + "\n")
        logger.info("Added GTK bookmark: %s", entry)
        return True
    except Exception as e:
        logger.error("Failed to write GTK bookmarks: %s", e)
        return False


def remove_gtk_bookmark(path: str) -> bool:
    """Removes the exact URI entry (with or without label) from GTK bookmarks."""
    if not os.path.exists(GTK_BOOKMARKS_PATH):
        return True

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        with open(GTK_BOOKMARKS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = [line for line in lines if not (line.strip() == uri or line.strip().startswith(uri + " "))]

        if len(lines) == len(new_lines):
            return True

        _atomic_write_text(GTK_BOOKMARKS_PATH, "".join(new_lines))
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
    if not os.path.exists(KDE_PLACES_PATH):
        logger.debug("KDE places file not found at %s", KDE_PLACES_PATH)
        return False

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(KDE_PLACES_PATH, parser=parser)
        root = tree.getroot()

        for bookmark in root.findall("bookmark"):
            if bookmark.get("href") == uri:
                logger.info("KDE place for %s already exists.", path)
                return True

        _backup_once(KDE_PLACES_PATH)

        new_bookmark = ET.SubElement(root, "bookmark", {"href": uri})
        title = ET.SubElement(new_bookmark, "title")
        title.text = label

        info = ET.SubElement(new_bookmark, "info")

        meta_fd = ET.SubElement(info, "metadata", {"owner": "http://freedesktop.org"})
        ET.SubElement(meta_fd, f"{{{NS_BOOKMARK}}}icon", {"name": "folder-remote"})

        meta_kde = ET.SubElement(info, "metadata", {"owner": "http://www.kde.org"})
        ET.SubElement(meta_kde, "ID").text = hashlib.sha1(path.encode("utf-8")).hexdigest()
        ET.SubElement(meta_kde, "isSystemItem").text = "false"

        _atomic_write_tree(tree, KDE_PLACES_PATH)
        logger.info("Added KDE place: %s", label)
        return True
    except Exception as e:
        logger.error("Failed to update KDE places: %s", e)
        return False


def remove_kde_place(path: str) -> bool:
    """Removes the bookmark entry for the given path from user-places.xbel."""
    if not os.path.exists(KDE_PLACES_PATH):
        return True

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{urllib.parse.quote(path)}"

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(KDE_PLACES_PATH, parser=parser)
        root = tree.getroot()

        to_remove = [b for b in root.findall("bookmark") if b.get("href") == uri]
        if not to_remove:
            return True

        _backup_once(KDE_PLACES_PATH)

        for bookmark in to_remove:
            root.remove(bookmark)

        _atomic_write_tree(tree, KDE_PLACES_PATH)
        logger.info("Removed KDE place for %s", path)
        return True
    except Exception as e:
        logger.error("Failed to remove KDE place: %s", e)
        return False


def integrate_mount(path: str, label: str = APP_NAME) -> None:
    """Helper to apply integration for all supported environments."""
    add_gtk_bookmark(path, label)
    add_kde_place(path, label)


def clean_integration(path: str) -> None:
    """Helper to remove integration from all supported environments."""
    remove_gtk_bookmark(path)
    remove_kde_place(path)
