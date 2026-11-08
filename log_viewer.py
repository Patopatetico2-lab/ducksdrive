"""
log_viewer.py - Real-Time Diagnostic Log Viewer for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Displays application and Rclone mount logs in real time with keyword filtering,
severity levels, log folder exploration, and one-click copy to clipboard.
"""

import glob
import logging
import os
from typing import Any, List, Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from rclone_settings import apply_app_theme, is_dark_theme_active, load_config

logger = logging.getLogger(__name__)

xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
LOG_DIR = os.path.join(xdg_data, "ducksdrive")
APP_LOG_FILE = os.path.join(LOG_DIR, "ducksdrive.log")


class LogViewerDialog(QDialog):
    """
    Integrated diagnostic log viewer window showing real-time rclone and application logs.
    """

    def __init__(self, parent: Optional[QWidget] = None, initial_file: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"DucksDrive - {tr('Logs de Diagnóstico')}")
        self.resize(780, 560)
        self.setMinimumSize(600, 400)

        self._active_file: Optional[str] = None
        self._last_file_size: int = 0
        self._cached_lines: List[str] = []

        self._init_ui()
        self.apply_theme()
        self._refresh_log_sources()

        if initial_file and os.path.exists(initial_file):
            self.source_combo.insertItem(0, f"Log: {os.path.basename(initial_file)}", initial_file)
            self.source_combo.setCurrentIndex(0)
            self._on_source_changed(0)

        # Polling timer to refresh logs in real time (every 1.5 seconds)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._check_file_updates)
        self._poll_timer.start()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # 1. Top Controls Bar: Source Selector + Severity Filter + Search Filter
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_source = QLabel(tr("Arquivo:"))
        top_bar.addWidget(lbl_source)

        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(220)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        top_bar.addWidget(self.source_combo)

        lbl_level = QLabel(tr("Nível:"))
        top_bar.addWidget(lbl_level)

        self.level_combo = QComboBox()
        self.level_combo.addItem(tr("Todos os Níveis"), "all")
        self.level_combo.addItem(tr("Avisos e Erros (WARNING / ERROR)"), "warn_err")
        self.level_combo.addItem(tr("Apenas Erros (ERROR)"), "error_only")
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        top_bar.addWidget(self.level_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("Filtrar logs... (ex: ERROR, failed, token, quota)"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        top_bar.addWidget(self.search_edit)

        main_layout.addLayout(top_bar)

        # 2. Main Log Text Display (Monospaced, Read-Only)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.text_edit.setFont(font)
        main_layout.addWidget(self.text_edit)

        # 3. Bottom Bar: Auto-scroll + File info + Copy + Open Folder + Close
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.autoscroll_checkbox = QCheckBox(tr("Rolar automaticamente para o final"))
        self.autoscroll_checkbox.setChecked(True)
        bottom_bar.addWidget(self.autoscroll_checkbox)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #7f8c8d; font-size: 8.5pt;")
        bottom_bar.addWidget(self.info_label)

        bottom_bar.addStretch()

        self.btn_copy = QPushButton(tr("Copiar para Área de Transferência"))
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        bottom_bar.addWidget(self.btn_copy)

        self.btn_open_folder = QPushButton(tr("Abrir Pasta de Logs"))
        self.btn_open_folder.setCursor(Qt.PointingHandCursor)
        self.btn_open_folder.clicked.connect(self._open_log_folder)
        bottom_bar.addWidget(self.btn_open_folder)

        self.btn_refresh = QPushButton(tr("Atualizar"))
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._force_reload)
        bottom_bar.addWidget(self.btn_refresh)

        self.btn_close = QPushButton(tr("Fechar"))
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        bottom_bar.addWidget(self.btn_close)

        main_layout.addLayout(bottom_bar)

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies theme styling to the dialog and code display."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        apply_app_theme(active_pref)
        is_dark = is_dark_theme_active(active_pref)

        if is_dark:
            bg_color = "#181a1c"
            fg_color = "#e6edf3"
            border_color = "#30363d"
        else:
            bg_color = "#f8f9fa"
            fg_color = "#1f2328"
            border_color = "#d0d7de"

        self.text_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {bg_color};
                color: {fg_color};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 6px;
            }}
        """)

    def _refresh_log_sources(self) -> None:
        """Discovers active and recent rclone log files and populates the source combo."""
        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        # 1. Main app log
        self.source_combo.addItem(tr("Log Geral do App (ducksdrive.log)"), APP_LOG_FILE)

        # 2. Per-remote rclone logs
        rclone_logs = sorted(
            glob.glob(os.path.join(LOG_DIR, "rclone-*.log")),
            key=os.path.getmtime,
            reverse=True,
        )
        for log_path in rclone_logs[:8]:
            base = os.path.basename(log_path)
            self.source_combo.addItem(f"Rclone: {base}", log_path)

        # 3. All rclone logs combined
        if rclone_logs:
            self.source_combo.addItem(tr("Todos os Logs do Rclone (Combinados)"), "combined_rclone")

        self.source_combo.blockSignals(False)

        # Default to first rclone log if available, otherwise app log
        if len(rclone_logs) > 0:
            self.source_combo.setCurrentIndex(1)
        else:
            self.source_combo.setCurrentIndex(0)
        self._on_source_changed(self.source_combo.currentIndex())

    def _on_source_changed(self, index: int) -> None:
        """Loads lines from selected file."""
        target = self.source_combo.currentData()
        self._active_file = target
        self._last_file_size = 0
        self._load_active_source()

    def _load_active_source(self) -> None:
        target = self._active_file
        if not target:
            self._cached_lines = []
            self._apply_filter()
            return

        lines: List[str] = []
        try:
            if target == "combined_rclone":
                rclone_logs = sorted(
                    glob.glob(os.path.join(LOG_DIR, "rclone-*.log")),
                    key=os.path.getmtime,
                )
                for lp in rclone_logs:
                    if os.path.exists(lp):
                        with open(lp, "r", encoding="utf-8", errors="replace") as f:
                            lines.extend(f.readlines())
            elif os.path.exists(target):
                self._last_file_size = os.path.getsize(target)
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            else:
                lines = [tr("Nenhum log encontrado para exibir.")]
        except Exception as exc:
            lines = [f"Error reading log file: {exc}"]

        # Keep last 1500 lines for smooth UI responsiveness
        self._cached_lines = lines[-1500:]
        self._apply_filter()

    def _check_file_updates(self) -> None:
        """Polls active file size for real-time updates."""
        if not self.isVisible():
            return
        target = self._active_file
        if not target or target == "combined_rclone":
            return
        if not os.path.exists(target):
            return
        try:
            sz = os.path.getsize(target)
            if sz != self._last_file_size:
                self._last_file_size = sz
                self._load_active_source()
        except OSError:
            pass

    def _force_reload(self) -> None:
        self._refresh_log_sources()

    def _apply_filter(self) -> None:
        query = self.search_edit.text().strip().lower()
        level_mode = str(self.level_combo.currentData() or "all")

        filtered: List[str] = []
        for line in self._cached_lines:
            lower = line.lower()
            if query and query not in lower:
                continue

            if level_mode == "error_only":
                if "error" not in lower and "fatal" not in lower:
                    continue
            elif level_mode == "warn_err":
                if "error" not in lower and "warn" not in lower and "failed" not in lower:
                    continue

            filtered.append(line)

        self.text_edit.setPlainText("".join(filtered))

        if self.autoscroll_checkbox.isChecked():
            sb = self.text_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

        self.info_label.setText(
            tr("%d linha(s) exibida(s)") % len(filtered)
        )

    def _copy_to_clipboard(self) -> None:
        content = self.text_edit.toPlainText()
        if not content:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(content)
            original_text = self.btn_copy.text()
            self.btn_copy.setText(tr("✓ Copiado com sucesso!"))
            self.btn_copy.setEnabled(False)
            QTimer.singleShot(1800, lambda: self._restore_copy_button(original_text))

    def _restore_copy_button(self, original_text: str) -> None:
        self.btn_copy.setText(original_text)
        self.btn_copy.setEnabled(True)

    def _open_log_folder(self) -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_DIR))

    def closeEvent(self, event: Any) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        super().closeEvent(event)
