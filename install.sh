#!/bin/bash

# install.sh - Installation script for rclone-drive-gui
# Supports Debian/Ubuntu (apt) and Arch Linux (pacman)

set -e

APP_NAME="rclone-drive-gui"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"

echo "=== Installing $APP_NAME ==="

# Verify that script is run from inside the repository directory containing main.py
if [ ! -f "main.py" ]; then
    echo "Error: 'main.py' not found in the current directory."
    echo "Please run this script from inside the cloned rclone-drive-gui directory."
    exit 1
fi

# 1. Detect Distribution and Install System Dependencies
if [ -f /etc/debian_version ]; then
    echo "Detected Debian/Ubuntu-based system."
    sudo apt update
    sudo apt install -y rclone fuse3 python3-venv python3-pip
elif [ -f /etc/arch-release ]; then
    echo "Detected Arch Linux-based system."
    sudo pacman -Syu --noconfirm rclone fuse3 python-pip
else
    echo "Unsupported distribution. Please ensure 'rclone', 'fuse3', and 'python3' are installed manually."
fi

# 2. Prepare Installation Directory
echo "Preparing installation directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp *.py "$INSTALL_DIR/"

# 3. Setup Python Virtual Environment
echo "Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

echo "Installing Python dependencies (PySide6)..."
pip install --upgrade pip
pip install PySide6

deactivate

# 4. Create Launcher Script
echo "Creating launcher script..."
mkdir -p "$BIN_DIR"
cat <<EOF > "$BIN_DIR/$APP_NAME"
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
python3 "$INSTALL_DIR/main.py" "\$@"
EOF
chmod +x "$BIN_DIR/$APP_NAME"

# 5. Create Desktop Entry
echo "Creating desktop entry..."
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=Rclone Drive GUI
Comment=Manage cloud drives with Rclone
Exec=$BIN_DIR/$APP_NAME
Icon=network-cloud
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
EOF

echo "=== Installation Complete ==="
echo "You can now run the app by typing '$APP_NAME' in your terminal or searching for 'Rclone Drive GUI' in your application menu."
echo "Note: If '$BIN_DIR' is not in your PATH, add 'export PATH=\$PATH:$BIN_DIR' to your .bashrc or .zshrc."
