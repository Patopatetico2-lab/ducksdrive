#!/bin/bash
set -e
APP_NAME="ducksdrive"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"

echo "=== Installing $APP_NAME ==="
if [ ! -f "main.py" ]; then
    echo "Error: 'main.py' not found."
    exit 1
fi

if [ -f /etc/debian_version ]; then
    echo "Detected Debian/Ubuntu-based system."
    sudo apt update && sudo apt install -y rclone fuse3 python3-venv python3-pip
elif [ -f /etc/arch-release ]; then
    echo "Detected Arch Linux-based system."
    sudo pacman -Syu --noconfirm rclone fuse3 python-pip
else
    echo "Error: Unsupported Linux distribution. Automatic dependency installation is only supported on Debian/Ubuntu and Arch Linux." >&2
    echo "Please ensure 'rclone', 'fuse3', 'python3-venv', and 'python3-pip' are installed manually." >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"
cp *.py icon.svg "$INSTALL_DIR/"

python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip
pip install PySide6
deactivate

mkdir -p "$BIN_DIR"
cat <<EOF> "$BIN_DIR/$APP_NAME"
#!/bin/bash
source "$INSTALL_DIR/venv/bin/activate"
python3 "$INSTALL_DIR/main.py" "\$@"
EOF
chmod +x "$BIN_DIR/$APP_NAME"

mkdir -p "$(dirname "$DESKTOP_FILE")"
cat <<EOF> "$DESKTOP_FILE"
[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
Exec=$BIN_DIR/$APP_NAME
Icon=$INSTALL_DIR/icon.svg
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=true
EOF

update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
echo "=== Installation Complete ==="
echo "You can now run the app by typing '$APP_NAME' in your terminal or searching for 'DucksDrive' in your application menu."
echo "Note: If '$BIN_DIR' is not in your PATH, add 'export PATH=\$PATH:$BIN_DIR' to your .bashrc or .zshrc."
