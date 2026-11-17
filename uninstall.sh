#!/bin/bash
APP_NAME="ducksdrive"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
INSTALL_DIR="$XDG_DATA_HOME/$APP_NAME"
BIN_FILE="${XDG_BIN_HOME:-$HOME/.local/bin}/$APP_NAME"
DESKTOP_FILE="$XDG_DATA_HOME/applications/$APP_NAME.desktop"
AUTOSTART_FILE="$XDG_CONFIG_HOME/autostart/$APP_NAME.desktop"
CONFIG_DIR="$XDG_CONFIG_HOME/$APP_NAME"
CLOUD_DRIVES_BASE="$HOME/CloudDrives"

echo "=== Uninstalling $APP_NAME ==="
echo "Stopping running application and rclone instances..."
pkill -f "[p]ython3.*ducksdrive" 2>/dev/null || true
pkill -x "ducksdrive" 2>/dev/null || true
pkill -f "[r]clone mount.*CloudDrives" 2>/dev/null || true
pkill -f "[r]clone mount.*GoogleDrive" 2>/dev/null || true
sleep 1 

# Dynamically unmount and clean up all mount points under ~/CloudDrives/
if [ -d "$CLOUD_DRIVES_BASE" ]; then
    shopt -s nullglob
    for mp in "$CLOUD_DRIVES_BASE"/*; do
        if [ -d "$mp" ]; then
            echo "Unmounting $mp..."
            fusermount3 -uz "$mp" 2>/dev/null || fusermount -uz "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
            if mountpoint -q "$mp" 2>/dev/null; then
                echo "WARNING: Mount point '$mp' is still actively mounted! Skipping directory removal." >&2
                continue
            fi
            if rmdir "$mp" 2>/dev/null; then
                echo "Removed mount point directory $mp"
            else
                echo "Kept '$mp' (not empty)."
            fi
        fi
    done
    shopt -u nullglob
    # Remove base CloudDrives directory if empty
    rmdir "$CLOUD_DRIVES_BASE" 2>/dev/null || true
fi

# Legacy fallback just in case
LEGACY_MOUNT="$HOME/GoogleDrive"
if mountpoint -q "$LEGACY_MOUNT" 2>/dev/null || [ -d "$LEGACY_MOUNT" ]; then
    echo "Unmounting legacy $LEGACY_MOUNT..."
    fusermount3 -uz "$LEGACY_MOUNT" 2>/dev/null || fusermount -uz "$LEGACY_MOUNT" 2>/dev/null || umount -l "$LEGACY_MOUNT" 2>/dev/null || true
    if mountpoint -q "$LEGACY_MOUNT" 2>/dev/null; then
        echo "WARNING: Legacy mount point '$LEGACY_MOUNT' is still actively mounted! Skipping directory removal." >&2
    else
        if [ -d "$LEGACY_MOUNT" ]; then
            if rmdir "$LEGACY_MOUNT" 2>/dev/null; then
                echo "Removed legacy mount point directory $LEGACY_MOUNT"
            else
                echo "Kept '$LEGACY_MOUNT' (not empty)."
            fi
        fi
    fi
fi

echo "Unmounting custom paths and removing orphaned GUI bookmarks..."

python3 - <<'PYEOF' 2>/dev/null || true
import json, os, re, shutil, subprocess, urllib.parse
xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

custom_paths = []
cfg_file = os.path.join(xdg_config, "ducksdrive", "config.json")
if os.path.exists(cfg_file):
    try:
        with open(cfg_file, encoding="utf-8") as f:
            cfg = json.load(f)
        remotes_cfg = cfg.get("remotes_config") if isinstance(cfg, dict) else {}
        if isinstance(remotes_cfg, dict):
            for r_cfg in remotes_cfg.values():
                if isinstance(r_cfg, dict):
                    mp_raw = str(r_cfg.get("custom_mount_point") or r_cfg.get("mount_path") or "").strip()
                    if mp_raw:
                        custom_paths.append(os.path.abspath(os.path.expanduser(mp_raw)))
    except Exception:
        pass

for mp in custom_paths:
    for tool, flag in (("fusermount3", "-uz"), ("fusermount", "-uz"), ("umount", "-l")):
        bin_path = shutil.which(tool)
        if bin_path:
            res = subprocess.run([bin_path, flag, mp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                break

custom_uris = [f"file://{urllib.parse.quote(mp)}" for mp in custom_paths]

for p in [os.path.join(xdg_data, "user-places.xbel"), os.path.join(xdg_config, "user-places.xbel")]:
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f: s = f.read()
        s = re.sub(r'<bookmark[^>]*href="file://[^"]*CloudDrives[^"]*".*?</bookmark>\s*', '', s, flags=re.DOTALL)
        s = re.sub(r'<separator>(?:(?!</separator>).)*?CloudDrives.*?</separator>\s*', '', s, flags=re.DOTALL)
        for uri in custom_uris:
            s = re.sub(rf'<bookmark[^>]*href="{re.escape(uri)}"[^>]*>.*?</bookmark>\s*', '', s, flags=re.DOTALL)
        with open(p, "w", encoding="utf-8") as f: f.write(s)
    bak = p + ".bak"
    if os.path.exists(bak):
        os.remove(bak)

gtk_bookmarks = os.path.join(xdg_config, "gtk-3.0", "bookmarks")
if os.path.exists(gtk_bookmarks):
    with open(gtk_bookmarks, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(gtk_bookmarks, "w", encoding="utf-8") as f:
        for line in lines:
            unquoted = urllib.parse.unquote(line)
            stripped = line.strip()
            is_custom = any(stripped == uri or stripped.startswith(uri + " ") for uri in custom_uris)
            if not is_custom and "CloudDrives" not in line and "GoogleDrive" not in line and "CloudDrives" not in unquoted and "GoogleDrive" not in unquoted:
                f.write(line)
PYEOF

echo "Removing application files and configuration..."
rm -rf "$INSTALL_DIR" "$BIN_FILE" "$DESKTOP_FILE" "$AUTOSTART_FILE" "$CONFIG_DIR"

RCLONE_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/rclone"
RCLONE_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/rclone"
if [ -d "$RCLONE_CONFIG_DIR" ] || [ -d "$RCLONE_CACHE_DIR" ]; then
    wipe_rclone=""
    if [ -t 0 ]; then
        read -r -p "Deseja remover também as configurações globais do Rclone ($RCLONE_CONFIG_DIR) e o cache VFS? (s/N): " wipe_rclone || true
    fi
    if [ "${wipe_rclone:-}" = "s" ] || [ "${wipe_rclone:-}" = "S" ] || [ "${wipe_rclone:-}" = "y" ] || [ "${wipe_rclone:-}" = "Y" ]; then
        rm -rf "$RCLONE_CONFIG_DIR" "$RCLONE_CACHE_DIR"
        echo "Removed global Rclone config and VFS cache."
    else
        echo "Kept global Rclone config ($RCLONE_CONFIG_DIR)."
    fi
fi

update-desktop-database "$XDG_DATA_HOME/applications/" 2>/dev/null || true
echo "=== Uninstallation Complete ==="
