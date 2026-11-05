#!/bin/bash
set -euo pipefail

echo "=== Building DucksDrive AppImage ==="

# Check dependencies
for cmd in pip curl unzip; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: '$cmd' is required."
        exit 1
    fi
done

# Detect Python environment
PYTHON_BIN="python3"
if [ -x "$HOME/.local/share/ducksdrive/venv/bin/python3" ]; then
    PYTHON_BIN="$HOME/.local/share/ducksdrive/venv/bin/python3"
elif [ -x "/usr/share/ducksdrive/venv/bin/python3" ]; then
    PYTHON_BIN="/usr/share/ducksdrive/venv/bin/python3"
fi

# Ensure PyInstaller is installed
if ! "$PYTHON_BIN" -m PyInstaller --version &> /dev/null; then
    echo "Installing PyInstaller..."
    "$PYTHON_BIN" -m pip install pyinstaller || pip install --user pyinstaller || pip install --user pyinstaller --break-system-packages
fi

# Run PyInstaller
echo "Running PyInstaller..."
"$PYTHON_BIN" -m PyInstaller --name ducksdrive --windowed --icon=icon.svg --add-data "icon.svg:." --hidden-import PySide6.QtSvg --noconfirm main.py

# Create AppDir
echo "Creating AppDir structure..."
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/scalable/apps

# Copy PyInstaller output to AppDir
cp -r dist/ducksdrive/* AppDir/usr/bin/

# Copy rclone statically to the AppDir so it runs anywhere
echo "Downloading rclone static binary for AppImage..."
curl -O https://downloads.rclone.org/v1.73.5/rclone-v1.73.5-linux-amd64.zip
unzip -j rclone-v1.73.5-linux-amd64.zip rclone-v1.73.5-linux-amd64/rclone -d AppDir/usr/bin/
rm rclone-v1.73.5-linux-amd64.zip
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
StartupWMClass=ducksdrive
DESKTOP
cp AppDir/usr/share/applications/ducksdrive.desktop AppDir/

# Create AppRun
cat << 'APPRUN' > AppDir/AppRun
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH:-}"
exec "${HERE}/usr/bin/ducksdrive" "$@"
APPRUN
chmod +x AppDir/AppRun

# Download appimagetool
echo "Downloading appimagetool..."
curl -L -O https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage

# Build AppImage
echo "Building AppImage..."
./appimagetool-x86_64.AppImage --appimage-extract-and-run AppDir DucksDrive-x86_64.AppImage

echo "=== Done! AppImage created as DucksDrive-x86_64.AppImage ==="
