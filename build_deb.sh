#!/bin/bash
set -euo pipefail

PKG_NAME="ducksdrive"
PKG_VER="1.0.7"
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
if [ -x /usr/share/ducksdrive/venv/bin/python3 ]; then
    exec /usr/share/ducksdrive/venv/bin/python3 /usr/share/ducksdrive/main.py "$@"
fi
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
Depends: python3, rclone, fuse3 | fuse, python3-pyside6.qtwidgets | python3-venv, python3-pyside6.qtgui | python3-pip, python3-pyside6.qtcore | python3-pip, python3-pyside6.qtsvg | libxcb-cursor0
Maintainer: DucksDrive <https://github.com/Patopatetico2-lab/ducksdrive>
Homepage: https://github.com/Patopatetico2-lab/ducksdrive
Description: Lightweight Linux system tray client for Rclone FUSE mounts
 DucksDrive is a Python (PySide6/Qt6) desktop client and GUI for mounting
 and managing cloud storage (Google Drive, OneDrive, Dropbox, Mega, S3,
 Nextcloud/WebDAV, SFTP) via rclone and FUSE3 on Linux.
EOF
chmod 644 "$CTRL_DIR/control"

cat << 'EOF' > "$CTRL_DIR/postinst"
#!/bin/sh
set -e
if ! /usr/bin/python3 -c "import PySide6" >/dev/null 2>&1; then
    if [ ! -x /usr/share/ducksdrive/venv/bin/python3 ]; then
        echo "PySide6 not found in system python3; creating isolated venv in /usr/share/ducksdrive/venv..."
        /usr/bin/python3 -m venv /usr/share/ducksdrive/venv
        /usr/share/ducksdrive/venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
        /usr/share/ducksdrive/venv/bin/pip install "PySide6>=6.6,<7"
    fi
fi
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
EOF
chmod 755 "$CTRL_DIR/postinst"

cat << 'EOF' > "$CTRL_DIR/prerm"
#!/bin/sh
set -e
rm -rf /usr/share/ducksdrive/venv /usr/share/ducksdrive/__pycache__ 2>/dev/null || true
EOF
chmod 755 "$CTRL_DIR/prerm"

if command -v dpkg-deb >/dev/null 2>&1; then
    mkdir -p "$ROOT_DIR/DEBIAN"
    cp -a "$CTRL_DIR"/* "$ROOT_DIR/DEBIAN/"
    dpkg-deb --root-owner-group --build "$ROOT_DIR" "$DEB_FILE"
else
    printf "2.0\n" > "$WORK_DIR/debian-binary"
    tar --owner=0 --group=0 -czf "$WORK_DIR/control.tar.gz" -C "$CTRL_DIR" .
    tar --owner=0 --group=0 -czf "$WORK_DIR/data.tar.gz" -C "$ROOT_DIR" .
    python3 - "$DEB_FILE" "$WORK_DIR/debian-binary" "$WORK_DIR/control.tar.gz" "$WORK_DIR/data.tar.gz" << 'PYEOF'
import os, sys, time

out_deb = sys.argv[1]
members = sys.argv[2:]
mtime = int(time.time())

with open(out_deb, "wb") as out:
    out.write(b"!<arch>\n")
    for path in members:
        name = os.path.basename(path).encode("ascii")
        with open(path, "rb") as f:
            data = f.read()
        size = len(data)
        # Standard BSD/Debian ar member header (no trailing slash in member name)
        header = (
            f"{name.decode('ascii'):<16}"
            f"{mtime:<12}"
            f"{0:<6}"
            f"{0:<6}"
            f"{'100644':<8}"
            f"{size:<10}"
            "`\n"
        ).encode("ascii")
        assert len(header) == 60
        out.write(header)
        out.write(data)
        if size % 2 == 1:
            out.write(b"\n")
PYEOF
fi

echo "=== Done! Package created: $DEB_FILE ==="
