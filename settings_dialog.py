"""
settings_dialog.py - Configuration Dialog for DucksDrive.

Provides a user-friendly settings dialog with safe GUI controls (QSpinBox, QComboBox)
organized into semantic QGroupBox sections for bandwidth, cache, and system preferences.
"""

import logging
import os
import re
import shutil
from typing import Optional

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget
)

from rclone_settings import CONFIG_DIR, load_config, save_config
from i18n import tr

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Settings dialog allowing users to adjust Rclone performance limits
    and system integration preferences using safe controls instead of free text inputs.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Configurações do DucksDrive"))
        self.setMinimumWidth(520)
        
        self.config = load_config()
        self._init_ui()
        self._load_values()

    def _init_ui(self) -> None:
        """Initializes grouped form layouts with QGroupBox controls."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        title_label = QLabel(tr("Preferências de Desempenho e Sistema"))
        title_label.setStyleSheet("font-size: 13pt; font-weight: bold;")
        main_layout.addWidget(title_label)

        # 1. Grupo Rede & Desempenho
        net_group = QGroupBox(tr("Rede e Transferência"))
        net_layout = QFormLayout(net_group)
        net_layout.setSpacing(10)

        self.bwlimit_combo = QComboBox()
        self.bwlimit_combo.addItem("Ilimitado (Sem teto de banda)", "off")
        self.bwlimit_combo.addItem("2 MB/s (Modesto)", "2M")
        self.bwlimit_combo.addItem("5 MB/s (Equilibrado)", "5M")
        self.bwlimit_combo.addItem("10 MB/s (Rápido)", "10M")
        self.bwlimit_combo.addItem("25 MB/s (Muito Rápido)", "25M")
        self.bwlimit_combo.addItem("50 MB/s (Máximo)", "50M")
        net_layout.addRow(tr("Limite de Banda:"), self.bwlimit_combo)

        self.transfers_spin = QSpinBox()
        self.transfers_spin.setRange(1, 16)
        self.transfers_spin.setValue(4)
        net_layout.addRow(tr("Transferências Paralelas:"), self.transfers_spin)

        main_layout.addWidget(net_group)

        # 2. Grupo Armazenamento & Cache
        cache_group = QGroupBox(tr("Armazenamento e Cache SSD"))
        cache_layout = QFormLayout(cache_group)
        cache_layout.setSpacing(10)

        self.vfs_mode_combo = QComboBox()
        self.vfs_mode_combo.addItem("Full (Recomendado)", "full")
        self.vfs_mode_combo.addItem("Writes", "writes")
        self.vfs_mode_combo.addItem("Minimal", "minimal")
        self.vfs_mode_combo.addItem("Off", "off")
        cache_layout.addRow(tr("Modo do Cache VFS:"), self.vfs_mode_combo)

        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(1, 500)
        self.cache_spin.setSuffix(" GB")
        self.cache_spin.setValue(2)
        cache_layout.addRow(tr("Tamanho Máximo do Cache VFS:"), self.cache_spin)

        main_layout.addWidget(cache_group)

        # 3. Grupo Geral / Sistema
        sys_group = QGroupBox(tr("Integração com o Sistema"))
        sys_layout = QVBoxLayout(sys_group)
        
        self.autostart_checkbox = QCheckBox(tr("Iniciar DucksDrive automaticamente com o sistema operativo"))
        sys_layout.addWidget(self.autostart_checkbox)

        # Language Selection
        lang_layout = QFormLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(tr("Português (Brasil)"), "pt_BR")
        self.lang_combo.addItem(tr("Inglês (EUA)"), "en_US")
        lang_layout.addRow(tr("Idioma / Language:"), self.lang_combo)
        sys_layout.addLayout(lang_layout)

        main_layout.addWidget(sys_group)

        # Buttons layout
        btn_layout = QHBoxLayout()

        self.btn_uninstall = QPushButton(tr("Desinstalar"))
        self.btn_uninstall.clicked.connect(self._on_uninstall)
        self.btn_uninstall.setStyleSheet(
            "background-color: #e74c3c; color: white; border: none; padding: 6px 14px; border-radius: 4px; font-weight: bold;"
        )
        btn_layout.addWidget(self.btn_uninstall)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton(tr("Cancelar"))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_cancel.setStyleSheet("padding: 6px 14px;")
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton(tr("Guardar Configurações"))
        self.btn_save.clicked.connect(self._save_and_accept)
        self.btn_save.setStyleSheet(
            "background-color: #3498db; color: white; border: none; padding: 6px 14px; border-radius: 4px; font-weight: bold;"
        )
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _load_values(self) -> None:
        """Populates form controls with values loaded from config with proper unit parsing."""
        bwlimit_val = str(self.config.get("bwlimit", "off"))
        index = self.bwlimit_combo.findData(bwlimit_val)
        if index >= 0:
            self.bwlimit_combo.setCurrentIndex(index)
        else:
            # Add custom bandwidth limit dynamically if not in presets
            self.bwlimit_combo.addItem(f"Personalizado ({bwlimit_val})", bwlimit_val)
            self.bwlimit_combo.setCurrentIndex(self.bwlimit_combo.count() - 1)

        transfers_val = int(self.config.get("transfers", 4))
        self.transfers_spin.setValue(transfers_val)

        cache_str = str(self.config.get("vfs_cache_max_size", "2G")).strip().upper()
        cache_gb = 2
        try:
            match = re.match(r"^(\d+)([KMGT]?)$", cache_str)
            if match:
                val, unit = match.groups()
                num = int(val)
                if unit == "K":
                    cache_gb = max(1, num // (1024 * 1024))
                elif unit == "M":
                    cache_gb = max(1, num // 1024)
                elif unit == "T":
                    cache_gb = num * 1024
                else:  # G or empty
                    cache_gb = num
            else:
                digits = ''.join(filter(str.isdigit, cache_str))
                if digits:
                    cache_gb = int(digits)
        except Exception:
            pass
        self.cache_spin.setValue(max(1, min(cache_gb, 500)))

        vfs_mode = str(self.config.get("vfs_cache_mode", "full"))
        index = self.vfs_mode_combo.findData(vfs_mode)
        if index >= 0:
            self.vfs_mode_combo.setCurrentIndex(index)

        self.autostart_checkbox.setChecked(bool(self.config.get("autostart", True)))

        lang = str(self.config.get("language", "pt_BR"))
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

    def _save_and_accept(self) -> None:
        """Validates controls, warns on high SSD cache size, saves to config, and closes."""
        bwlimit = self.bwlimit_combo.currentData() or "off"
        transfers = self.transfers_spin.value()
        cache_gb = self.cache_spin.value()
        cache_size = f"{cache_gb}G"
        vfs_mode = self.vfs_mode_combo.currentData() or "full"
        language = self.lang_combo.currentData() or "pt_BR"
        language_changed = language != str(self.config.get("language", "pt_BR"))

        # Validate high cache size warning if >= 50 GB
        if cache_gb >= 50:
            res = QMessageBox.warning(
                self,
                tr("Aviso de Cache Elevado"),
                tr("Atenção: Um tamanho de cache VFS de %s GB é muito elevado e corre o risco de esgotar o espaço do seu SSD!\n\nDeseja prosseguir?") % cache_gb,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if res != QMessageBox.Yes:
                return

        autostart = self.autostart_checkbox.isChecked()

        # Update config dict
        self.config["bwlimit"] = bwlimit
        self.config["vfs_cache_max_size"] = cache_size
        self.config["transfers"] = transfers
        self.config["vfs_cache_mode"] = vfs_mode
        self.config["autostart"] = autostart
        self.config["language"] = language

        # Save to disk
        if save_config(self.config):
            QMessageBox.information(self, tr("Configuração Guardada"), tr("Preferências atualizadas com sucesso.\nAs alterações serão aplicadas na próxima conexão."))
            if language_changed:
                QMessageBox.information(
                    self,
                    tr("Reinício Necessário"),
                    tr("O idioma foi alterado. Reinicie o DucksDrive para aplicar o novo idioma.")
                )
            self.accept()
        else:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao gravar o ficheiro de configuração."))

    def _on_uninstall(self) -> None:
        """Asks for confirmation, removes autostart entry and config directory, then quits."""
        res = QMessageBox.question(
            self,
            tr("Desinstalar DucksDrive"),
            tr("Tem a certeza de que deseja remover todas as configurações, montagens, atalhos e ficheiros da aplicação do DucksDrive?\n\nA aplicação será encerrada e desinstalada."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res != QMessageBox.Yes:
            return

        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        
        autostart_file = os.path.join(xdg_config, "autostart", "ducksdrive.desktop")
        config_dir = CONFIG_DIR
        app_share_dir = os.path.join(xdg_data, "ducksdrive")
        desktop_file = os.path.join(xdg_data, "applications", "ducksdrive.desktop")
        bin_file = os.path.expanduser("~/.local/bin/ducksdrive")
        appimage_path = os.environ.get("APPIMAGE")
        import subprocess
        cloud_drives_base = os.path.expanduser("~/CloudDrives")
        if os.path.isdir(cloud_drives_base):
            for item in os.listdir(cloud_drives_base):
                mp = os.path.join(cloud_drives_base, item)
                if os.path.isdir(mp):
                    subprocess.run(["fusermount3", "-uz", mp], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    subprocess.run(["fusermount", "-uz", mp], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    subprocess.run(["umount", "-l", mp], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    try:
                        os.rmdir(mp)
                    except OSError:
                        pass
            try:
                os.rmdir(cloud_drives_base)
            except OSError:
                pass

        # Remove orphaned GUI bookmarks
        for p in [os.path.expanduser("~/.local/share/user-places.xbel"), os.path.expanduser("~/.config/user-places.xbel")]:
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        s = f.read()
                    s = re.sub(r'<bookmark[^>]*href="file://[^"]*CloudDrives[^"]*".*?</bookmark>', '', s, flags=re.DOTALL)
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(s)
                except Exception:
                    pass

        gtk_bookmarks = os.path.expanduser("~/.config/gtk-3.0/bookmarks")
        if os.path.exists(gtk_bookmarks):
            try:
                with open(gtk_bookmarks, encoding="utf-8") as f:
                    lines = f.readlines()
                with open(gtk_bookmarks, "w", encoding="utf-8") as f:
                    for line in lines:
                        if "CloudDrives" not in line and "GoogleDrive" not in line:
                            f.write(line)
            except Exception:
                pass

        try:
            if os.path.exists(autostart_file):
                os.remove(autostart_file)
                logger.info("Removed autostart file: %s", autostart_file)
            if os.path.isdir(config_dir):
                shutil.rmtree(config_dir)
                logger.info("Removed config directory: %s", config_dir)
            if os.path.isdir(app_share_dir):
                shutil.rmtree(app_share_dir)
                logger.info("Removed app share directory: %s", app_share_dir)
            if os.path.exists(desktop_file):
                os.remove(desktop_file)
                subprocess.run(["update-desktop-database", os.path.dirname(desktop_file)], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                logger.info("Removed desktop file: %s", desktop_file)
            if os.path.exists(bin_file):
                os.remove(bin_file)
                logger.info("Removed bin file: %s", bin_file)
            if appimage_path and os.path.exists(appimage_path):
                os.remove(appimage_path)
                logger.info("Removed AppImage: %s", appimage_path)
        except Exception as e:
            logger.error("Failed to uninstall DucksDrive: %s", e)
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao remover alguns ficheiros:") + f"\n{e}")
            return

        self.reject()
        QApplication.quit()
