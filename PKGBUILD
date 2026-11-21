# Maintainer: DucksDrive <https://github.com/Patopatetico2-lab/ducksdrive>
pkgname=ducksdrive
pkgver=1.1.2
pkgrel=1
pkgdesc="Lightweight Linux system tray client for Rclone FUSE mounts (Google Drive, OneDrive, etc.)"
arch=('any')
url="https://github.com/Patopatetico2-lab/ducksdrive"
license=('MIT')
depends=('python' 'pyside6' 'qt6-svg' 'rclone' 'fuse3')

package() {
    install -dm755 "$pkgdir/usr/share/$pkgname"
    cp -r "$startdir/ducksdrive" "$pkgdir/usr/share/$pkgname/"
    find "$pkgdir/usr/share/$pkgname/ducksdrive" -type d -name "__pycache__" -exec rm -rf {} +
    find "$pkgdir/usr/share/$pkgname/ducksdrive" -type f -name "*.pyc" -delete

    install -Dm644 "$startdir/ducksdrive/assets/icon.svg" "$pkgdir/usr/share/$pkgname/icon.svg"
    install -Dm644 "$startdir/ducksdrive/assets/icon.svg" "$pkgdir/usr/share/icons/hicolor/scalable/apps/$pkgname.svg"
    install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"

    install -dm755 "$pkgdir/usr/bin"
    cat << 'EOF' > "$pkgdir/usr/bin/ducksdrive"
#!/bin/sh
exec /usr/bin/python3 -c "import sys; sys.path.insert(0, '/usr/share/ducksdrive'); from ducksdrive.main import main; main()" "$@"
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
StartupNotify=false
StartupWMClass=ducksdrive
EOF
    chmod 644 "$pkgdir/usr/share/applications/ducksdrive.desktop"
}
