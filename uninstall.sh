#!/bin/bash

# uninstall.sh - Complete uninstallation script for rclone-drive-gui

set -e

APP_NAME="rclone-drive-gui"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_FILE="$HOME/.local/bin/$APP_NAME"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"
MOUNT_POINT="$HOME/GoogleDrive"

echo "=== Uninstalling $APP_NAME ==="

# 1. Stop any running instances of the app
echo "Stopping running application instances..."
pkill -f "main.py" 2>/dev/null || true
pkill -f "$APP_NAME" 2>/dev/null || true

# 2. Unmount virtual drive if active
if mountpoint -q "$MOUNT_POINT" 2>/dev/null || [ -d "$MOUNT_POINT" ]; then
    echo "Unmounting $MOUNT_POINT..."
    fusermount3 -u "$MOUNT_POINT" 2>/dev/null || \
    fusermount -u "$MOUNT_POINT" 2>/dev/null || \
    umount -l "$MOUNT_POINT" 2>/dev/null || true
    
    # Remove mount directory
    rm -rf "$MOUNT_POINT"
    echo "Mount point removed."
fi

# 3. Remove application files and directories
echo "Removing application files from $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo "Removing terminal binary from $BIN_FILE..."
rm -f "$BIN_FILE"

echo "Removing desktop launcher from $DESKTOP_FILE..."
rm -f "$DESKTOP_FILE"

echo "=== Uninstallation Complete ==="
echo "$APP_NAME has been completely removed from your system."
