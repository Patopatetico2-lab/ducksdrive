#!/bin/bash
set -euo pipefail

echo "=== Building DucksDrive AppImage ==="

# Check dependencies
if ! command -v pip &> /dev/null; then
    echo "Error: pip is required."
    exit 1
fi

# Install PyInstaller
pip install --user pyinstaller

# Run PyInstaller
echo "Running PyInstaller..."
python3 -m pyinstaller --name ducksdrive --windowed --icon=icon.svg --add-data "icon.svg:." main.py

# Create AppDir
echo "Creating AppDir structure..."
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/scalable/apps

# Copy PyInstaller output to AppDir
cp -r dist/ducksdrive/* AppDir/usr/bin/

# Copy rclone statically to the AppDir so it runs anywhere
echo "Downloading rclone static binary for AppImage..."
curl -O https://downloads.rclone.org/v1.66.0/rclone-v1.66.0-linux-amd64.zip
unzip -j rclone-v1.66.0-linux-amd64.zip rclone-v1.66.0-linux-amd64/rclone -d AppDir/usr/bin/
rm rclone-v1.66.0-linux-amd64.zip
chmod +x AppDir/usr/bin/rclone

# Copy icon
cp icon.svg AppDir/usr/share/icons/hicolor/scalable/apps/ducksdrive.svg
cp icon.svg AppDir/ducksdrive.svg

# Create desktop file
cat << 'DESKTOP' > AppDir/usr/share/applications/ducksdrive.desktop
[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec=ducksdrive
Icon=ducksdrive
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
DESKTOP
cp AppDir/usr/share/applications/ducksdrive.desktop AppDir/

# Create AppRun
cat << 'APPRUN' > AppDir/AppRun
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
exec "${HERE}/usr/bin/ducksdrive" "$@"
APPRUN
chmod +x AppDir/AppRun

# Download appimagetool
echo "Downloading appimagetool..."
curl -L -O https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage

# Build AppImage
echo "Building AppImage..."
./appimagetool-x86_64.AppImage AppDir DucksDrive-x86_64.AppImage

echo "=== Done! AppImage created as DucksDrive-x86_64.AppImage ==="
