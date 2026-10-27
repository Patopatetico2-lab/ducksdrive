#!/bin/bash
set -euo pipefail

PKG_NAME="ducksdrive"
PKG_VER="1.0.6"
PKG_REL="1"
DEB_FILE="${PKG_NAME}_${PKG_VER}-${PKG_REL}_all.deb"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "=== Building Debian/Ubuntu package: $DEB_FILE ==="

ROOT_DIR="$WORK_DIR/root"
CTRL_DIR="$WORK_DIR/control"

mkdir -p "$ROOT_DIR/usr/bin"
mkdir -p "$ROOT_DIR/usr/share/$PKG_NAME"
mkdir -p "$ROOT_DIR/usr/share/applications"
mkdir -p "$ROOT_DIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$ROOT_DIR/usr/share/doc/$PKG_NAME"
mkdir -p "$CTRL_DIR"

install -Dm644 ./*.py -t "$ROOT_DIR/usr/share/$PKG_NAME/"
install -Dm644 ./icon.svg "$ROOT_DIR/usr/share/$PKG_NAME/icon.svg"
install -Dm644 ./icon.svg "$ROOT_DIR/usr/share/icons/hicolor/scalable/apps/$PKG_NAME.svg"
install -Dm644 ./LICENSE "$ROOT_DIR/usr/share/doc/$PKG_NAME/copyright"

cat << 'EOF' > "$ROOT_DIR/usr/bin/ducksdrive"
#!/bin/sh
exec /usr/bin/python3 /usr/share/ducksdrive/main.py "$@"
EOF
chmod 755 "$ROOT_DIR/usr/bin/ducksdrive"

cat << 'EOF' > "$ROOT_DIR/usr/share/applications/ducksdrive.desktop"
[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec=/usr/bin/ducksdrive
Icon=ducksdrive
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
StartupWMClass=ducksdrive
EOF
chmod 644 "$ROOT_DIR/usr/share/applications/ducksdrive.desktop"

INSTALLED_SIZE="$(du -sk "$ROOT_DIR" | awk '{print $1}')"

cat << EOF > "$CTRL_DIR/control"
Package: $PKG_NAME
Version: ${PKG_VER}-${PKG_REL}
Section: utils
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Depends: python3, python3-pyside6.qtwidgets | python3-pyside6, python3-pyside6.qtgui | python3-pyside6, python3-pyside6.qtcore | python3-pyside6, rclone, fuse3
Maintainer: DucksDrive <https://github.com/Patopatetico2-lab/ducksdrive>
Homepage: https://github.com/Patopatetico2-lab/ducksdrive
Description: Lightweight Linux system tray client for Rclone FUSE mounts
 DucksDrive is a Python (PySide6/Qt6) desktop client and GUI for mounting
 and managing cloud storage (Google Drive, OneDrive, Dropbox, Mega, S3,
 Nextcloud/WebDAV, SFTP) via rclone and FUSE3 on Linux.
EOF
chmod 644 "$CTRL_DIR/control"

if command -v dpkg-deb >/dev/null 2>&1; then
    mkdir -p "$ROOT_DIR/DEBIAN"
    cp "$CTRL_DIR/control" "$ROOT_DIR/DEBIAN/control"
    dpkg-deb --root-owner-group --build "$ROOT_DIR" "$DEB_FILE"
else
    echo "2.0" > "$WORK_DIR/debian-binary"
    tar --owner=0 --group=0 -czf "$WORK_DIR/control.tar.gz" -C "$CTRL_DIR" .
    tar --owner=0 --group=0 -czf "$WORK_DIR/data.tar.gz" -C "$ROOT_DIR" .
    rm -f "$DEB_FILE"
    ar rcs "$DEB_FILE" "$WORK_DIR/debian-binary" "$WORK_DIR/control.tar.gz" "$WORK_DIR/data.tar.gz"
fi

echo "=== Done! Package created: $DEB_FILE ==="
