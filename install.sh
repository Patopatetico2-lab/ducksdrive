#!/bin/bash
set -euo pipefail
APP_NAME="ducksdrive"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
INSTALL_DIR="$XDG_DATA_HOME/$APP_NAME"
DESKTOP_FILE="$XDG_DATA_HOME/applications/$APP_NAME.desktop"
AUTOSTART_FILE="$XDG_CONFIG_HOME/autostart/$APP_NAME.desktop"

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
    if pacman -Q rclone fuse3 pyside6 qt6-svg &>/dev/null; then
        echo "System dependencies (rclone, fuse3, pyside6, qt6-svg) are already installed."
    else
        sudo pacman -S --needed --noconfirm rclone fuse3 pyside6 qt6-svg
    fi
elif [ -f /etc/fedora-release ] || [ -f /etc/redhat-release ]; then
    echo "Detected Fedora/RHEL-based system."
    sudo dnf install -y rclone fuse3 python3-pip
elif [ -f /etc/SuSE-release ] || grep -qi "suse" /etc/os-release 2>/dev/null; then
    echo "Detected openSUSE-based system."
    sudo zypper refresh && sudo zypper install -y rclone fuse3 python3-pip
else
    echo "Warning: Unsupported Linux distribution. Automatic dependency installation is not supported for this OS." >&2
    echo "Please ensure 'rclone', 'fuse3', 'python3-venv', and 'python3-pip' are installed manually." >&2
    response=""
    read -p "Deseja tentar prosseguir com a configuração do ambiente Python mesmo assim? (s/N): " response || true
    if [ "${response:-}" = "s" ] || [ "${response:-}" = "S" ]; then
        echo "Prosseguindo com a instalação..."
    else
        echo "Instalação cancelada."
        exit 1
    fi
fi

mkdir -p "$INSTALL_DIR"
cp main.py gui_tray.py status_popup.py settings_dialog.py rclone_daemon.py rclone_stats.py rclone_config.py rclone_settings.py filemanager_integration.py i18n.py log_viewer.py remote_settings_dialog.py notifications.py updater.py vfs_inhibitor.py keyring_manager.py "$INSTALL_DIR/"
[ -f icon.svg ] && cp icon.svg "$INSTALL_DIR/"

if /usr/bin/python3 -c "import PySide6.QtWidgets, PySide6.QtSvg" >/dev/null 2>&1; then
    echo "Using system PySide6 (native Qt6/KDE/GNOME theme integration)."
else
    echo "System PySide6 not found; setting up isolated Python venv..."
    python3 -m venv "$INSTALL_DIR/venv"
    source "$INSTALL_DIR/venv/bin/activate"
    pip install --upgrade pip
    pip install "PySide6>=6.6,<7"
    deactivate
fi

mkdir -p "$BIN_DIR"
cat <<EOF> "$BIN_DIR/$APP_NAME"
#!/bin/bash
if [ -x "$INSTALL_DIR/venv/bin/python3" ] && "$INSTALL_DIR/venv/bin/python3" -c "import PySide6" >/dev/null 2>&1; then
    exec "$INSTALL_DIR/venv/bin/python3" -c "import sys; sys.path.insert(0, '$INSTALL_DIR'); import main; main.main()" "\$@"
fi
exec /usr/bin/python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR'); import main; main.main()" "\$@"
EOF
chmod +x "$BIN_DIR/$APP_NAME"

mkdir -p "$(dirname "$DESKTOP_FILE")"
cat <<EOF> "$DESKTOP_FILE"
[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
TryExec=$BIN_DIR/$APP_NAME
Exec=$BIN_DIR/$APP_NAME
Icon=$INSTALL_DIR/icon.svg
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=false
StartupWMClass=ducksdrive
EOF

# Create autostart entry unless user explicitly disabled autostart in config.json
AUTOSTART_ENABLED="true"
CFG_FILE="$XDG_CONFIG_HOME/$APP_NAME/config.json"
if [ -f "$CFG_FILE" ] && python3 -c "import json, sys; sys.exit(0 if json.load(open('$CFG_FILE')).get('autostart', True) else 1)" 2>/dev/null; then
    AUTOSTART_ENABLED="true"
elif [ -f "$CFG_FILE" ]; then
    AUTOSTART_ENABLED="false"
fi

if [ "$AUTOSTART_ENABLED" = "true" ]; then
    mkdir -p "$(dirname "$AUTOSTART_FILE")"
    cat <<EOF> "$AUTOSTART_FILE"
[Desktop Entry]
Name=DucksDrive
Comment=Manage cloud drives with Rclone
TryExec=$BIN_DIR/$APP_NAME
Exec=$BIN_DIR/$APP_NAME --autostart
Icon=$INSTALL_DIR/icon.svg
Terminal=false
Type=Application
Categories=Network;Utility;
StartupNotify=false
Hidden=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
X-KDE-autostart-after=panel
EOF
fi

update-desktop-database "$(dirname "$DESKTOP_FILE")" 2>/dev/null || true
echo "=== Installation Complete ==="
echo "You can now run the app by typing '$APP_NAME' in your terminal or searching for 'DucksDrive' in your application menu."
echo "Note: If '$BIN_DIR' is not in your PATH, add 'export PATH=\$PATH:$BIN_DIR' to your .bashrc or .zshrc."
