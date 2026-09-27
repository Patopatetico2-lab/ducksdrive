#!/bin/bash
APP_NAME="ducksdrive"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_FILE="$HOME/.local/bin/$APP_NAME"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/$APP_NAME.desktop"
CONFIG_DIR="$HOME/.config/$APP_NAME"
MOUNT_POINT="$HOME/GoogleDrive"

echo "=== Uninstalling $APP_NAME ==="
echo "Stopping running application and rclone instances..."
pkill -f "ducksdrive.*main\.py" 2>/dev/null || true
pkill -f "$APP_NAME" 2>/dev/null || true
pkill -f "rclone mount.*$MOUNT_POINT" 2>/dev/null || true
sleep 1 

if mountpoint -q "$MOUNT_POINT" 2>/dev/null || [ -d "$MOUNT_POINT" ]; then
    echo "Unmounting $MOUNT_POINT..."
    fusermount3 -uz "$MOUNT_POINT" 2>/dev/null || fusermount -uz "$MOUNT_POINT" 2>/dev/null || umount -l "$MOUNT_POINT" 2>/dev/null || true
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        echo "ERROR: Mount point '$MOUNT_POINT' is still actively mounted!" >&2
        exit 1
    fi
    if [ -d "$MOUNT_POINT" ]; then
        rm -rf "$MOUNT_POINT"
    fi
fi

echo "Removing application files and configuration..."
rm -rf "$INSTALL_DIR" "$BIN_FILE" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$CONFIG_DIR"
update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
echo "=== Uninstallation Complete ==="
