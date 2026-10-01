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
pkill -f "ducksdrive.*main\.py" 2>/dev/null || true
pkill -f "$APP_NAME" 2>/dev/null || true
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
            rm -rf "$mp"
            echo "Removed mount point directory $mp"
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
            rm -rf "$LEGACY_MOUNT"
            echo "Removed legacy mount point directory $LEGACY_MOUNT"
        fi
    fi
fi

echo "Removing application files and configuration..."
rm -rf "$INSTALL_DIR" "$BIN_FILE" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$CONFIG_DIR"
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
echo "=== Uninstallation Complete ==="
