#!/bin/bash
APP_NAME="ducksdrive"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_FILE="$HOME/.local/bin/$APP_NAME"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/$APP_NAME.desktop"
CONFIG_DIR="$HOME/.config/$APP_NAME"
CLOUD_DRIVES_BASE="$HOME/CloudDrives"

echo "=== Uninstalling $APP_NAME ==="
echo "Stopping running application and rclone instances..."
pkill -f "python3.*ducksdrive" 2>/dev/null || true
pkill -f "rclone mount.*CloudDrives" 2>/dev/null || true
pkill -f "rclone mount.*GoogleDrive" 2>/dev/null || true
sleep 1 

# Dynamically unmount and clean up all mount points under ~/CloudDrives/
if [ -d "$CLOUD_DRIVES_BASE" ]; then
    shopt -s nullglob
    for mp in "$CLOUD_DRIVES_BASE"/*; do
        if [ -d "$mp" ]; then
            echo "Unmounting $mp..."
            fusermount3 -uz "$mp" 2>/dev/null || fusermount -uz "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
            if mountpoint -q "$mp" 2>/dev/null; then
                echo "WARNING: Mount point '$mp' is still actively mounted! Skipping directory removal." >&2
                continue
            fi
            if rmdir "$mp" 2>/dev/null; then
                echo "Removed mount point directory $mp"
            else
                echo "Kept '$mp' (not empty)."
            fi
        fi
    done
    shopt -u nullglob
    # Remove base CloudDrives directory if empty
    rmdir "$CLOUD_DRIVES_BASE" 2>/dev/null || true
fi

# Legacy fallback just in case
LEGACY_MOUNT="$HOME/GoogleDrive"
if mountpoint -q "$LEGACY_MOUNT" 2>/dev/null || [ -d "$LEGACY_MOUNT" ]; then
    echo "Unmounting legacy $LEGACY_MOUNT..."
    fusermount3 -uz "$LEGACY_MOUNT" 2>/dev/null || fusermount -uz "$LEGACY_MOUNT" 2>/dev/null || umount -l "$LEGACY_MOUNT" 2>/dev/null || true
    if mountpoint -q "$LEGACY_MOUNT" 2>/dev/null; then
        echo "WARNING: Legacy mount point '$LEGACY_MOUNT' is still actively mounted! Skipping directory removal." >&2
    else
        if [ -d "$LEGACY_MOUNT" ]; then
            if rmdir "$LEGACY_MOUNT" 2>/dev/null; then
                echo "Removed legacy mount point directory $LEGACY_MOUNT"
            else
                echo "Kept '$LEGACY_MOUNT' (not empty)."
            fi
        fi
    fi
fi

echo "Removing orphaned GUI bookmarks..."
GTK_BOOKMARKS="$HOME/.config/gtk-3.0/bookmarks"

python3 - <<'PYEOF' 2>/dev/null || true
import os, re
for p in [os.path.expanduser("~/.local/share/user-places.xbel"), os.path.expanduser("~/.config/user-places.xbel")]:
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f: s = f.read()
        s = re.sub(r'<bookmark[^>]*href="file://[^"]*CloudDrives[^"]*".*?</bookmark>', '', s, flags=re.DOTALL)
        with open(p, "w", encoding="utf-8") as f: f.write(s)
PYEOF

GTK_BOOKMARKS="$HOME/.config/gtk-3.0/bookmarks"
if [ -f "$GTK_BOOKMARKS" ]; then
    # Double quotes on purpose: $HOME must be expanded before sed sees the pattern
    sed -i "\~^file://$HOME/CloudDrives/~d" "$GTK_BOOKMARKS" 2>/dev/null || true
    sed -i "\~^file://$HOME/GoogleDrive\([[:space:]]\|\$\)~d" "$GTK_BOOKMARKS" 2>/dev/null || true
fi


echo "Removing application files and configuration..."
rm -rf "$INSTALL_DIR" "$BIN_FILE" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$CONFIG_DIR"
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
echo "=== Uninstallation Complete ==="
