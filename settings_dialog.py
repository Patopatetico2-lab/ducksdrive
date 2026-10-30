"""
settings_dialog.py - Configuration Dialog for DucksDrive.

Provides a user-friendly settings dialog with safe GUI controls (QSpinBox, QComboBox)
organized into semantic QGroupBox sections for bandwidth, cache, and system preferences.
"""

import logging
import os
import re
import shutil
from typing import Any, Optional

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

from rclone_config import TaskRunner
from rclone_settings import (
    CONFIG_DIR,
    apply_app_theme,
    clear_vfs_cache_files,
    get_vfs_cache_usage_bytes,
    load_config,
    save_config,
)
from rclone_stats import fetch_vfs_queue, format_bytes
from i18n import set_language, tr

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Settings dialog allowing users to adjust Rclone performance limits
    and system integration preferences using safe controls instead of free text inputs.
    """

    def __init__(self, parent: Optional[QWidget] = None, daemon: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.daemon = daemon
        self._cache_runner = TaskRunner(self)
        self.language_changed = False
        self.bwlimit_changed = False
        self.theme_changed = False
        self.setWindowTitle(tr("Configurações do DucksDrive"))
        self.setMinimumWidth(520)
        
        self.config = load_config()
        self._init_ui()
        self._load_values()
        self.apply_theme(str(self.theme_combo.currentData() or self.config.get("theme") or "auto"))
        self._refresh_cache_usage()

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
        self.bwlimit_combo.addItem(tr("Ilimitado (Sem teto de banda)"), "off")
        self.bwlimit_combo.addItem(tr("2 MB/s (Modesto)"), "2M")
        self.bwlimit_combo.addItem(tr("5 MB/s (Equilibrado)"), "5M")
        self.bwlimit_combo.addItem(tr("10 MB/s (Rápido)"), "10M")
        self.bwlimit_combo.addItem(tr("25 MB/s (Muito Rápido)"), "25M")
        self.bwlimit_combo.addItem(tr("50 MB/s (Máximo)"), "50M")
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
        self.vfs_mode_combo.addItem(tr("Full (Recomendado)"), "full")
        self.vfs_mode_combo.addItem(tr("Writes"), "writes")
        self.vfs_mode_combo.addItem(tr("Minimal"), "minimal")
        self.vfs_mode_combo.addItem(tr("Off"), "off")
        self.vfs_mode_combo.currentIndexChanged.connect(self._on_vfs_mode_changed)
        cache_layout.addRow(tr("Modo do Cache VFS:"), self.vfs_mode_combo)

        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(1, 500)
        self.cache_spin.setSuffix(" GB")
        self.cache_spin.setValue(2)
        cache_layout.addRow(tr("Tamanho Máximo do Cache VFS:"), self.cache_spin)

        cache_usage_row = QHBoxLayout()
        self.cache_usage_label = QLabel(tr("Calculando espaço..."))
        self.cache_usage_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        self.btn_clear_cache = QPushButton(tr("Limpar Cache Agora"))
        self.btn_clear_cache.clicked.connect(self._on_clear_cache)
        cache_usage_row.addWidget(self.cache_usage_label)
        cache_usage_row.addStretch()
        cache_usage_row.addWidget(self.btn_clear_cache)
        cache_layout.addRow(tr("Uso Atual do Cache no SSD:"), cache_usage_row)

        main_layout.addWidget(cache_group)

        # 3. Grupo Geral / Sistema
        sys_group = QGroupBox(tr("Integração com o Sistema"))
        sys_layout = QVBoxLayout(sys_group)
        
        self.autostart_checkbox = QCheckBox(tr("Iniciar DucksDrive automaticamente com o sistema operativo"))
        sys_layout.addWidget(self.autostart_checkbox)

        # Language & Theme Selection
        lang_layout = QFormLayout()
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(tr("Português (Brasil)"), "pt_BR")
        self.lang_combo.addItem(tr("Inglês (EUA)"), "en_US")
        lang_layout.addRow(tr("Idioma / Language:"), self.lang_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("Automático (Sistema)"), "auto")
        self.theme_combo.addItem(tr("Claro (Light)"), "light")
        self.theme_combo.addItem(tr("Escuro (Dark)"), "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        lang_layout.addRow(tr("Tema do Painel:"), self.theme_combo)
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
            self.bwlimit_combo.addItem(tr("Personalizado (%s)") % bwlimit_val, bwlimit_val)
            self.bwlimit_combo.setCurrentIndex(self.bwlimit_combo.count() - 1)

        try:
            transfers_val = int(self.config.get("transfers", 4))
        except (ValueError, TypeError):
            transfers_val = 4
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
        self._on_vfs_mode_changed()

        self.autostart_checkbox.setChecked(bool(self.config.get("autostart", True)))

        lang = str(self.config.get("language", "pt_BR"))
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

        theme = str(self.config.get("theme", "auto"))
        t_idx = self.theme_combo.findData(theme)
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)

    def _on_vfs_mode_changed(self, _index: int = 0) -> None:
        """Disables max VFS cache size spinbox when cache mode is 'off' or 'minimal'."""
        mode = self.vfs_mode_combo.currentData()
        self.cache_spin.setEnabled(mode not in ("off", "minimal"))

    def _on_theme_combo_changed(self, _index: int = 0) -> None:
        """Applies the selected theme immediately when the user changes the theme combo."""
        selected_theme = str(self.theme_combo.currentData() or "auto")
        self.apply_theme(selected_theme)

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies native QPalette styling for the specified theme ('light', 'dark', or 'auto')."""
        active_pref = str(theme_pref or self.theme_combo.currentData() or self.config.get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def reject(self) -> None:
        """Restores the previously saved theme if the user cancels the dialog."""
        apply_app_theme(str(self.config.get("theme") or "auto"))
        super().reject()

    def _refresh_cache_usage(self) -> None:
        """Asynchronously calculates local VFS cache usage in a background thread."""
        self.cache_usage_label.setText(tr("Calculando espaço..."))
        self._cache_runner.submit(
            get_vfs_cache_usage_bytes,
            callback=lambda usage: self.cache_usage_label.setText(format_bytes(int(usage or 0))),
        )

    def _on_clear_cache(self) -> None:
        """Safely clears local VFS cache files in background if no uploads are waiting in queue."""
        self.btn_clear_cache.setEnabled(False)
        creds = (
            self.daemon.rc_credentials
            if (self.daemon is not None and self.daemon.is_running() and self.daemon.rc_credentials)
            else None
        )

        def _check_before_clear(rc_creds: Any) -> tuple[int, int]:
            usage = get_vfs_cache_usage_bytes()
            pending = 0
            if usage > 0 and rc_creds is not None:
                pending = int(fetch_vfs_queue(rc_creds) or 0)
            return usage, pending

        self._cache_runner.submit(_check_before_clear, creds, callback=self._on_pre_clear_checked)

    def _on_pre_clear_checked(self, result: Any) -> None:
        if not isinstance(result, tuple):
            self.btn_clear_cache.setEnabled(True)
            return
        usage, pending = result
        self.cache_usage_label.setText(format_bytes(usage))

        if usage <= 0:
            self.btn_clear_cache.setEnabled(True)
            QMessageBox.information(self, tr("Limpar Cache VFS"), tr("O cache local VFS já está vazio."))
            return

        if pending > 0:
            self.btn_clear_cache.setEnabled(True)
            QMessageBox.warning(
                self,
                tr("Cache VFS em Uso"),
                tr("Existem %d arquivo(s) aguardando envio para a nuvem.\nAguarde o término dos envios antes de limpar o cache.") % pending,
            )
            return

        res = QMessageBox.question(
            self,
            tr("Limpar Cache VFS"),
            tr("Deseja remover %s de arquivos temporários em cache no SSD?\n(Seus arquivos na nuvem não serão afetados.)") % format_bytes(usage),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            self.btn_clear_cache.setEnabled(True)
            return

        def _do_clear() -> int:
            clear_vfs_cache_files()
            return get_vfs_cache_usage_bytes()

        self._cache_runner.submit(_do_clear, callback=self._on_cache_cleared)

    def _on_cache_cleared(self, remaining_bytes: Any) -> None:
        self.cache_usage_label.setText(format_bytes(int(remaining_bytes or 0)))
        self.btn_clear_cache.setEnabled(True)
        QMessageBox.information(self, tr("Cache Limpo"), tr("O cache VFS local foi limpo com sucesso."))

    def done(self, r: int) -> None:
        self._cache_runner.wait_all(2000)
        super().done(r)

    def _save_and_accept(self) -> None:
        """Validates controls, warns on high SSD cache size, saves to config, and closes."""
        bwlimit = self.bwlimit_combo.currentData() or "off"
        transfers = self.transfers_spin.value()
        cache_gb = self.cache_spin.value()
        cache_size = f"{cache_gb}G"
        vfs_mode = self.vfs_mode_combo.currentData() or "full"
        language = self.lang_combo.currentData() or "pt_BR"
        theme = self.theme_combo.currentData() or "auto"
        language_changed = language != str(self.config.get("language", "pt_BR"))
        theme_changed = theme != str(self.config.get("theme", "auto"))
        bwlimit_changed = bwlimit != str(self.config.get("bwlimit", "off"))
        mount_opts_changed = (
            cache_size != str(self.config.get("vfs_cache_max_size", "2G"))
            or int(transfers) != int(self.config.get("transfers", 4))
            or vfs_mode != str(self.config.get("vfs_cache_mode", "full"))
        )

        # Validate high cache size warning if >= 50 GB
        if self.cache_spin.isEnabled() and cache_gb >= 50:
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
        self.config["theme"] = theme

        # Save to disk
        if save_config(self.config):
            self.language_changed = language_changed
            self.theme_changed = theme_changed
            self.bwlimit_changed = bwlimit_changed
            self.apply_theme(theme)
            if language_changed:
                set_language(language)
            if mount_opts_changed:
                QMessageBox.information(
                    self,
                    tr("Configuração Guardada"),
                    tr("Preferências atualizadas com sucesso.\nAs alterações de cache/transferências serão aplicadas na próxima conexão.")
                )
            else:
                QMessageBox.information(
                    self,
                    tr("Configuração Guardada"),
                    tr("Preferências atualizadas e aplicadas imediatamente.")
                )
            self.accept()
        else:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao gravar o ficheiro de configuração."))

    def _on_uninstall(self) -> None:
        """Asks for confirmation, stops active mounts, removes all app/rclone configs, caches, shortcuts, and quits."""
        res = QMessageBox.question(
            self,
            tr("Desinstalar DucksDrive"),
            tr("Tem a certeza de que deseja remover todas as configurações, montagens, atalhos e ficheiros da aplicação do DucksDrive?\n\nA aplicação será encerrada e desinstalada."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if res != QMessageBox.Yes:
            return

        import subprocess
        from rclone_daemon import unmount_fuse_path, is_path_mounted
        from rclone_config import get_rclone_conf_path

        # 1. Cleanly stop the running daemon first so rclone exits and releases mount points and log files
        if self.daemon is not None:
            try:
                if hasattr(self.daemon, "health_timer") and self.daemon.health_timer:
                    self.daemon.health_timer.stop()
                self.daemon.stop()
            except Exception:
                pass

        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))

        autostart_file = os.path.join(xdg_config, "autostart", "ducksdrive.desktop")
        config_dir = CONFIG_DIR
        rclone_config_dir = os.path.join(xdg_config, "rclone")
        rclone_conf_file = get_rclone_conf_path()
        rclone_cache_dir = os.path.join(xdg_cache, "rclone")
        app_share_dir = os.path.join(xdg_data, "ducksdrive")
        desktop_file = os.path.join(xdg_data, "applications", "ducksdrive.desktop")
        bin_file = os.path.expanduser("~/.local/bin/ducksdrive")
        appimage_path = os.environ.get("APPIMAGE")

        # 2. Unmount and remove all directories under ~/CloudDrives
        cloud_drives_base = os.path.expanduser("~/CloudDrives")
        if os.path.isdir(cloud_drives_base):
            for item in os.listdir(cloud_drives_base):
                mp = os.path.join(cloud_drives_base, item)
                if os.path.isdir(mp):
                    unmount_fuse_path(mp, lazy=True)
                    if not is_path_mounted(mp):
                        try:
                            os.rmdir(mp)
                        except OSError:
                            pass
            try:
                os.rmdir(cloud_drives_base)
            except OSError:
                pass

        # 3. Remove orphaned GUI bookmarks and .bak files
        for p in [
            os.path.join(xdg_data, "user-places.xbel"),
            os.path.join(xdg_config, "user-places.xbel"),
        ]:
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        s = f.read()
                    s = re.sub(r'<bookmark[^>]*href="file://[^"]*CloudDrives[^"]*".*?</bookmark>\s*', '', s, flags=re.DOTALL)
                    s = re.sub(r'<separator>(?:(?!</separator>).)*?CloudDrives.*?</separator>\s*', '', s, flags=re.DOTALL)
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(s)
                except Exception:
                    pass
            bak_p = f"{p}.bak"
            if os.path.exists(bak_p):
                try:
                    os.remove(bak_p)
                except OSError:
                    pass

        gtk_bookmarks = os.path.join(xdg_config, "gtk-3.0", "bookmarks")
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

        # 4. Detach and close file logging handlers before deleting app_share_dir
        for log_name in (None, "ducksdrive"):
            lg = logging.getLogger(log_name)
            for handler in list(lg.handlers):
                if isinstance(handler, logging.FileHandler):
                    lg.removeHandler(handler)
                    try:
                        handler.close()
                    except Exception:
                        pass

        # 5. Remove all application, configuration, and cache files
        try:
            if os.path.exists(autostart_file):
                os.remove(autostart_file)
            if os.path.isdir(config_dir):
                shutil.rmtree(config_dir)
            for rc_file in (rclone_conf_file, f"{rclone_conf_file}.bak"):
                if rc_file and os.path.exists(rc_file):
                    try:
                        os.remove(rc_file)
                    except OSError:
                        pass
            if os.path.isdir(rclone_config_dir):
                shutil.rmtree(rclone_config_dir, ignore_errors=True)
            if os.path.isdir(rclone_cache_dir):
                shutil.rmtree(rclone_cache_dir, ignore_errors=True)
            if os.path.isdir(app_share_dir):
                shutil.rmtree(app_share_dir)
            if os.path.exists(desktop_file):
                os.remove(desktop_file)
                update_db_bin = shutil.which("update-desktop-database")
                if update_db_bin:
                    subprocess.run([update_db_bin, os.path.dirname(desktop_file)], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            if os.path.exists(bin_file):
                os.remove(bin_file)
            if appimage_path and os.path.exists(appimage_path):
                os.remove(appimage_path)
        except Exception as e:
            QMessageBox.critical(self, tr("Erro"), tr("Falha ao remover alguns ficheiros:") + f"\n{e}")
            return

        if os.path.exists("/usr/bin/ducksdrive") and not appimage_path:
            QMessageBox.information(
                self,
                tr("Desinstalar DucksDrive"),
                tr("Dados e montagens removidos.\nComo o pacote foi instalado via gerenciador do sistema, execute no terminal:\nsudo pacman -R ducksdrive")
            )

        self.reject()
        QApplication.quit()
