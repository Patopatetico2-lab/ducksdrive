"""
filemanager_integration.py - Desktop integration for file manager sidebars.

Provides functions to inject and remove the virtual drive path from:
1. KDE (Dolphin) sidebars via `~/.config/user-places.xbel` (XML) with automatic .bak backup.
2. GTK (GNOME/XFCE/Nautilus/Thunar) sidebars via `~/.config/gtk-3.0/bookmarks`.
"""

import logging
import os
import shutil
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# Register XML namespaces explicitly to prevent ns0/ns1 prefix corruption in KDE xbel files
ET.register_namespace("", "http://www.freedesktop.org/standards/shared-mime-info")
ET.register_namespace("kbel", "http://www.kde.org/standards/kbel/1.0")

APP_NAME = "DucksDrive"
GTK_BOOKMARKS_PATH = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
KDE_PLACES_PATH = os.path.expanduser("~/.config/user-places.xbel")


def add_gtk_bookmark(path: str, label: str = APP_NAME) -> bool:
    """
    Adds a URI entry to GTK 3.0 bookmarks file.
    Format: file:///home/user/Path Label
    """
    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{path}"
    entry = f"{uri} {label}"

    os.makedirs(os.path.dirname(GTK_BOOKMARKS_PATH), exist_ok=True)

    lines = []
    if os.path.exists(GTK_BOOKMARKS_PATH):
        with open(GTK_BOOKMARKS_PATH, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]

    # Check if already exists
    if any(line.startswith(uri) for line in lines):
        logger.info("GTK bookmark for %s already exists.", path)
        return True

    lines.append(entry)
    try:
        with open(GTK_BOOKMARKS_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("Added GTK bookmark: %s", entry)
        return True
    except Exception as e:
        logger.error("Failed to write GTK bookmarks: %s", e)
        return False


def remove_gtk_bookmark(path: str) -> bool:
    """Removes any URI entry starting with the given path from GTK bookmarks."""
    if not os.path.exists(GTK_BOOKMARKS_PATH):
        return True

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{path}"

    try:
        with open(GTK_BOOKMARKS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = [line for line in lines if not line.strip().startswith(uri)]

        if len(lines) == len(new_lines):
            return True

        with open(GTK_BOOKMARKS_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        logger.info("Removed GTK bookmark for %s", path)
        return True
    except Exception as e:
        logger.error("Failed to remove GTK bookmark: %s", e)
        return False


def add_kde_place(path: str, label: str = APP_NAME) -> bool:
    """
    Adds an entry to KDE's user-places.xbel (with automatic .bak backup).
    Injects a <bookmark> element with the appropriate metadata.
    """
    if not os.path.exists(KDE_PLACES_PATH):
        logger.debug("KDE places file not found at %s", KDE_PLACES_PATH)
        return False

    path = os.path.abspath(os.path.expanduser(path))
    uri = f"file://{path}"

    try:
        # Create backup before modifying user-places.xbel
        shutil.copy2(KDE_PLACES_PATH, f"{KDE_PLACES_PATH}.bak")
    except Exception as e:
        logger.warning("Failed to create backup of user-places.xbel: %s", e)

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        tree = ET.parse(KDE_PLACES_PATH, parser=parser)
        root = tree.getroot()

        # Check for existing
        for bookmark in root.findall("bookmark"):
            if bookmark.get("href") == uri:
                logger.info("KDE place for %s already exists.", path)
                return True

        # Create new bookmark element
        new_bookmark = ET.SubElement(root, "bookmark", {"href": uri})
        title = ET.SubElement(new_bookmark, "title")
        title.text = label
        
        info = ET.SubElement(new_bookmark, "info")
        metadata = ET.SubElement(info, "metadata", {"owner": "http://freedesktop.org"})
        
        # Standard KDE place attributes
        ET.SubElement(metadata, "{http://www.kde.org/standards/kbel/1.0}ID").text = str(hash(path))
        ET.SubElement(metadata, "{http://www.kde.org/standards/kbel/1.0}isSystemItem").text = "false"
        ET.SubElement(metadata, "{http://www.freedesktop.org/standards/shared-mime-info}icon", {"name": "folder-remote"})

        tree.write(KDE_PLACES_PATH, encoding="utf-8", xml_declaration=True)
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
    uri = f"file://{path}"

    try:
        # Create backup before modifying
        shutil.copy2(KDE_PLACES_PATH, f"{KDE_PLACES_PATH}.bak")
    except Exception as e:
        logger.warning("Failed to create backup of user-places.xbel: %s", e)

    try:
        tree = ET.parse(KDE_PLACES_PATH)
        root = tree.getroot()

        to_remove = []
        for bookmark in root.findall("bookmark"):
            if bookmark.get("href") == uri:
                to_remove.append(bookmark)

        if not to_remove:
            return True

        for bookmark in to_remove:
            root.remove(bookmark)

        tree.write(KDE_PLACES_PATH, encoding="utf-8", xml_declaration=True)
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
