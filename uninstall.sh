#!/bin/bash

# uninstall.sh - Complete uninstallation script for ducksdrive

set -e

APP_NAME="ducksdrive"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_FILE="$HOME/.local/bin/$APP_NAME"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"
AUTOSTART_FILE="$HOME/.config/autostart/$APP_NAME.desktop"
MOUNT_POINT="$HOME/GoogleDrive"

echo "=== Uninstalling $APP_NAME ==="

# 1. Stop any running instances of the app
echo "Stopping running application instances..."
pkill -f "ducksdrive/main.py" 2>/dev/null || true
pkill -f "$APP_NAME" 2>/dev/null || true

# 2. Unmount virtual drive if active with strict safety validation
if mountpoint -q "$MOUNT_POINT" 2>/dev/null || [ -d "$MOUNT_POINT" ]; then
    echo "Unmounting $MOUNT_POINT..."
    fusermount3 -u "$MOUNT_POINT" 2>/dev/null || \
    fusermount -u "$MOUNT_POINT" 2>/dev/null || \
    umount -l "$MOUNT_POINT" 2>/dev/null || true

    # CRITICAL SAFETY CHECK: Verify that the mountpoint is no longer active
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        echo "ERROR: Mount point '$MOUNT_POINT' is still actively mounted!" >&2
        echo "Aborting uninstallation to prevent data loss on the cloud." >&2
        exit 1
    fi
    
    if [ -d "$MOUNT_POINT" ]; then
        rm -rf "$MOUNT_POINT"
        echo "Mount point directory removed safely."
    fi
fi

# Also check for any dynamic DucksDrive mount points
for mp in "$HOME"/DucksDrive*; do
    if [ -d "$mp" ]; then
        echo "Unmounting $mp..."
        fusermount3 -u "$mp" 2>/dev/null || fusermount -u "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
        if mountpoint -q "$mp" 2>/dev/null; then
            echo "ERROR: Mount point '$mp' is still actively mounted! Aborting." >&2
            exit 1
        fi
        rm -rf "$mp"
        echo "Removed mount point $mp"
    fi
done

# 3. Remove application files and directories
echo "Removing application files from $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo "Removing terminal binary from $BIN_FILE..."
rm -f "$BIN_FILE"

echo "Removing desktop launcher from $DESKTOP_FILE..."
rm -f "$DESKTOP_FILE"

echo "Removing autostart entry from $AUTOSTART_FILE..."
rm -f "$AUTOSTART_FILE"

echo "=== Uninstallation Complete ==="
echo "$APP_NAME has been completely removed from your system."
