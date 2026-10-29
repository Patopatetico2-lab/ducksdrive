# Maintainer: DucksDrive <https://github.com/Patopatetico2-lab/ducksdrive>
pkgname=ducksdrive
pkgver=1.0.7
pkgrel=1
pkgdesc="Lightweight Linux system tray client for Rclone FUSE mounts (Google Drive, OneDrive, etc.)"
arch=('any')
url="https://github.com/Patopatetico2-lab/ducksdrive"
license=('MIT')
depends=('python' 'python-pyside6' 'qt6-svg' 'rclone' 'fuse3')

package() {
    install -dm755 "$pkgdir/usr/share/$pkgname"
    install -Dm644 "$startdir"/*.py -t "$pkgdir/usr/share/$pkgname/"

    install -Dm644 "$startdir/icon.svg" "$pkgdir/usr/share/$pkgname/icon.svg"
    install -Dm644 "$startdir/icon.svg" "$pkgdir/usr/share/icons/hicolor/scalable/apps/$pkgname.svg"
    install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"

    install -dm755 "$pkgdir/usr/bin"
    cat << 'EOF' > "$pkgdir/usr/bin/ducksdrive"
#!/bin/sh
exec /usr/bin/python3 /usr/share/ducksdrive/main.py "$@"
EOF
    chmod 755 "$pkgdir/usr/bin/ducksdrive"

    install -dm755 "$pkgdir/usr/share/applications"
    cat << 'EOF' > "$pkgdir/usr/share/applications/ducksdrive.desktop"
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
    chmod 644 "$pkgdir/usr/share/applications/ducksdrive.desktop"
}
