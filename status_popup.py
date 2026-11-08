"""
status_popup.py - Native-like floating status panel for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Provides a frameless popup window (Qt.Tool) displaying real-time transfer metrics,
progress bars, active file info, a rolling speed history chart, and quick actions.
"""

import logging
import os
import sys
import time
from collections import deque
from typing import Any, Optional

from i18n import tr
from PySide6.QtCore import Qt, QUrl, QTimer, QThread, QEvent, QPoint, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QLinearGradient,
    QPen,
    QBrush,
    QCursor,
    QDesktopServices,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
    QScrollArea
)

from rclone_config import TaskRunner
from rclone_daemon import RcloneDaemon, is_path_mounted
from rclone_settings import is_dark_theme_active
from rclone_stats import StatsData, format_bytes, rc_vfs_refresh, set_rc_bwlimit

logger = logging.getLogger(__name__)


class SpeedChartWidget(QWidget):
    """Custom lightweight line chart rendering rolling speed history peaks."""

    def __init__(self, max_points: int = 30, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMaximumHeight(80)
        self.setMinimumHeight(60)
        self.max_points = max_points
        self.is_dark = False
        self.speed_history: deque[float] = deque([0.0] * max_points, maxlen=max_points)

    def add_speed_point(self, speed_mb_s: float) -> None:
        """Appends a new speed data point and triggers a repaint only when visible."""
        self.speed_history.append(speed_mb_s)
        if self.isVisible():
            self.update()

    def paintEvent(self, event: Any) -> None:
        """Renders a smooth gradient-filled line chart of transfer speeds."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # Background card styling
        painter.setPen(Qt.NoPen)
        bg_color = QColor(38, 46, 56) if self.is_dark else QColor(245, 247, 250)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, 8, 8)

        if not self.speed_history or max(self.speed_history) == 0:
            # Draw subtle empty state text
            painter.setPen(QColor(149, 165, 166) if self.is_dark else QColor(150, 150, 150))
            painter.setFont(QFont("Sans Serif", 9))
            painter.drawText(rect, Qt.AlignCenter, tr("Sem velocidade de transferência ativa"))
            return

        max_speed = max(self.speed_history)
        if max_speed < 1.0:
            max_speed = 1.0  # Minimum scale

        # Construct path for line and gradient fill
        path = QPainterPath()
        fill_path = QPainterPath()

        points = []
        n = len(self.speed_history)
        for i, val in enumerate(self.speed_history):
            x = (i / max(1, n - 1)) * (width - 10) + 5
            y = height - 10 - (val / max_speed) * (height - 25)
            points.append((x, y))

        if points:
            fill_path.moveTo(points[0][0], height - 5)
            fill_path.lineTo(points[0][0], points[0][1])
            path.moveTo(points[0][0], points[0][1])

            for x, y in points[1:]:
                path.lineTo(x, y)
                fill_path.lineTo(x, y)

            fill_path.lineTo(points[-1][0], height - 5)
            fill_path.closeSubpath()

            # Gradient fill under the curve
            gradient = QLinearGradient(0, 0, 0, height)
            gradient.setColorAt(0.0, QColor(41, 128, 185, 80))
            gradient.setColorAt(1.0, QColor(41, 128, 185, 5))
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.NoPen)
            painter.drawPath(fill_path)

            # Draw speed curve line
            pen = QPen(QColor(41, 128, 185), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        painter.end()


# In-memory quota cache per remote to prevent UI flicker and "Quota indisponível" on transient network hiccups
_quota_cache: dict[str, tuple[int, int]] = {}


class QuotaWorkerThread(QThread):
    """Background worker thread to fetch cloud storage quota without blocking UI."""
    quota_fetched = Signal(object, object)  # total, used
    quota_failed = Signal()

    def __init__(self, credentials: Any, remote_name: str, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.credentials = credentials
        self.remote_name = remote_name

    def run(self) -> None:
        import json
        import urllib.request
        import base64
        import time

        try:
            creds = self.credentials
            auth_str = f"{creds.user}:{creds.password}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {b64_auth}", "Content-Type": "application/json"}

            endpoint_about = f"{creds.url}/operations/about"
            fs_arg = self.remote_name if self.remote_name.endswith(":") else f"{self.remote_name}:"
            req_about = urllib.request.Request(
                url=endpoint_about,
                data=json.dumps({"fs": fs_arg}).encode("utf-8"),
                headers=headers,
                method="POST"
            )

            # Up to 2 retries (3 total attempts) with a short delay on transient failures
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req_about, timeout=8) as response:
                        data = json.loads(response.read().decode("utf-8"))
                        total = int(data.get("total") or 0)
                        used = int(data.get("used") or 0)

                    if (data.get("total") is not None or data.get("used") is not None) and (total >= 0 and used >= 0):
                        self.quota_fetched.emit(total, used)
                        return
                    else:
                        break
                except Exception as attempt_err:
                    logger.debug("Quota attempt %d/3 failed: %s", attempt + 1, attempt_err)
                    if attempt < 2:
                        time.sleep(1.5)

            self.quota_failed.emit()
        except Exception as e:
            logger.debug("Failed to fetch quota: %s", e)
            self.quota_failed.emit()


class TransferItem(QWidget):
    """Small widget representing a single file transfer in the list."""

    def __init__(self, name: str, percentage: int, is_dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.file_name = name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self.name_label = QLabel(name)
        self.name_label.setWordWrap(False)
        layout.addWidget(self.name_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(percentage)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.apply_theme(is_dark)

    def apply_theme(self, is_dark: bool) -> None:
        text_col = "#ecf0f1" if is_dark else "#2c3e50"
        self.name_label.setStyleSheet(f"font-size: 9pt; color: {text_col};")

    def update_data(self, name: str, percentage: int) -> None:
        self.file_name = name
        self.name_label.setText(name)
        self.progress.setValue(percentage)


class RecentTransferItem(QWidget):
    """Small widget representing a completed file transfer in recent history."""

    def __init__(self, name: str, size_str: str, is_dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(6)

        self.name_label = QLabel(f"✓  {name}")
        self.name_label.setWordWrap(False)
        self.size_label = QLabel(size_str)
        self.size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.size_label, 0)
        self.apply_theme(is_dark)

    def apply_theme(self, is_dark: bool) -> None:
        name_col = "#ecf0f1" if is_dark else "#2c3e50"
        size_col = "#95a5a6" if is_dark else "#7f8c8d"
        self.name_label.setStyleSheet(f"font-size: 8.5pt; color: {name_col};")
        self.size_label.setStyleSheet(f"font-size: 8pt; color: {size_col};")

    def update_data(self, name: str, size_str: str) -> None:
        self.name_label.setText(f"✓  {name}")
        self.size_label.setText(size_str)


def _get_window_icon() -> QIcon:
    """Returns the application icon without importing gui_tray to avoid circular imports."""
    current_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(current_dir, "icon.svg")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        if not icon.isNull():
            return icon
    return QIcon.fromTheme("network-cloud", QIcon.fromTheme("folder-remote"))


class StatusPopup(QWidget):
    """
    Floating status panel acting as a native system tray popup window or standalone dashboard window.
    Supports both tray mode (Qt.Tool) and window mode (Qt.Window) for environments without a tray (e.g. GNOME Vanilla).
    """

    def __init__(
        self,
        daemon: Optional[RcloneDaemon] = None,
        parent: Optional[QWidget] = None,
        window_mode: bool = False,
        daemons: Optional[dict[str, RcloneDaemon]] = None,
    ) -> None:
        self.window_mode = window_mode
        self._tray: Optional[Any] = None
        if window_mode:
            super().__init__(parent, Qt.Window)
            self.setWindowTitle(f"DucksDrive - {tr('Painel de Controle')}")
            self.setWindowIcon(_get_window_icon())
            self.setMinimumWidth(360)
        else:
            super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setFixedWidth(340)

        self.daemons: dict[str, RcloneDaemon] = daemons.copy() if daemons else {}
        self._fallback_daemon: Optional[RcloneDaemon] = daemon
        if daemon is not None:
            clean_name = (daemon.current_remote or "default").rstrip(":")
            self.daemons[clean_name] = daemon
        self.current_remote_name: Optional[str] = next(iter(self.daemons.keys())) if self.daemons else None
        self._remote_stats: dict[str, StatsData] = {}

        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.is_paused = False

        self._last_quota_update = 0
        self._quota_remote: Optional[str] = None
        self._quota_timer = QTimer(self)
        self._quota_timer.setInterval(10 * 60 * 1000)  # 10 minutes cache
        self._quota_timer.timeout.connect(self._fetch_quota)
        # Note: quota timer is started in showEvent and stopped in hideEvent to avoid background polling when hidden

        self._quota_worker: Optional[QuotaWorkerThread] = None
        self._old_quota_workers: list[QuotaWorkerThread] = []
        self._last_quota_values: Optional[tuple[int, int]] = None
        self._pause_runner = TaskRunner(self)
        self._pause_busy = False
        self.is_dark = False
        self._theme_pref: Optional[str] = None
        self._applied_theme_state: Optional[tuple[bool, str, str]] = None
        self._latest_stats: Optional[StatsData] = None
        self._drag_pos: Optional[QPoint] = None
        self._user_moved = False

        self._init_ui()
        self.apply_theme()
        self.update_mounted_remotes()
        if daemon is not None:
            daemon.state_changed.connect(lambda s: self._on_daemon_state_changed(s, getattr(daemon, "current_remote", None)))
        for r, d in list(self.daemons.items()):
            d.state_changed.connect(lambda s, rem=r: self._on_daemon_state_changed(s, rem))

    @property
    def _window_mode(self) -> bool:
        return self.window_mode

    @property
    def active_remote(self) -> str:
        return self.current_remote_name or ""

    @property
    def daemon(self) -> RcloneDaemon:
        active = self.get_active_daemon()
        if active is not None:
            return active
        if self._fallback_daemon is not None:
            return self._fallback_daemon
        self._fallback_daemon = RcloneDaemon(parent=self)
        return self._fallback_daemon

    @daemon.setter
    def daemon(self, value: Optional[RcloneDaemon]) -> None:
        self._fallback_daemon = value
        if value is not None:
            clean_name = (value.current_remote or "default").rstrip(":")
            self.daemons[clean_name] = value
            if not self.current_remote_name:
                self.current_remote_name = clean_name

    def get_active_daemon(self) -> Optional[RcloneDaemon]:
        if self.current_remote_name and self.current_remote_name in self.daemons:
            return self.daemons[self.current_remote_name]
        for d in self.daemons.values():
            if d.is_running():
                return d
        if self.daemons:
            return next(iter(self.daemons.values()))
        return self._fallback_daemon

    def register_daemon(self, remote_name: str, daemon: RcloneDaemon) -> None:
        clean = remote_name.rstrip(":")
        self.daemons[clean] = daemon
        daemon.state_changed.connect(lambda s, rem=clean: self._on_daemon_state_changed(s, rem))
        self.update_mounted_remotes()

    def _init_ui(self) -> None:
        """Initializes the popup layout, widgets, and styling."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # Apply clean modern card styling
        self.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                color: #2c3e50;
                font-family: 'Sans Serif';
                font-size: 10pt;
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 8px 12px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QProgressBar {
                border: none;
                background-color: #ecf0f1;
                height: 6px;
                border-radius: 3px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #27ae60;
                border-radius: 3px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
        """)

        # 1. Header (Account & Status + Menu Button + Close Button)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.title_label = QLabel("<b>DucksDrive</b>")
        self.title_label.setStyleSheet("font-size: 11pt; color: #2c3e50;")
        header_layout.addWidget(self.title_label)

        self.btn_menu = QPushButton(tr("Menu ▾"))
        self.btn_menu.setFixedHeight(24)
        self.btn_menu.setCursor(Qt.PointingHandCursor)
        self.btn_menu.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 2px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        header_layout.addWidget(self.btn_menu)
        header_layout.addStretch()

        self.status_badge = QLabel(tr("● Offline"))
        self.status_badge.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 9pt; margin-right: 6px;")
        header_layout.addWidget(self.status_badge)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip(tr("Fechar Painel (ou pressione Esc)"))
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: #ecf0f1;
                color: #7f8c8d;
                border: none;
                border-radius: 11px;
                font-weight: bold;
                font-size: 9pt;
                padding: 0;
            }
            QPushButton:hover {
                background: #e74c3c;
                color: white;
            }
        """)
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)
        if self.window_mode:
            self.btn_close.hide()

        main_layout.addLayout(header_layout)

        # Multi-Mount Cloud Drive Selector
        self.selector_bar = QHBoxLayout()
        self.selector_bar.setContentsMargins(0, 0, 0, 0)
        self.selector_bar.setSpacing(6)

        self.lbl_cloud = QLabel(tr("Nuvem:"))
        self.lbl_cloud.setStyleSheet("font-size: 8.5pt; font-weight: bold; color: #7f8c8d;")
        self.selector_bar.addWidget(self.lbl_cloud)

        self.remote_combo = QComboBox()
        self.remote_combo.setFixedHeight(26)
        self.remote_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 8.5pt;
                font-weight: bold;
                background-color: transparent;
            }
        """)
        self.remote_combo.currentIndexChanged.connect(self._on_remote_combo_changed)
        self.selector_bar.addWidget(self.remote_combo, 1)

        main_layout.addLayout(self.selector_bar)

        # 2. Quota Section
        quota_layout = QVBoxLayout()
        quota_layout.setSpacing(2)
        self.quota_label = QLabel(tr("Desconectado"))
        self.quota_label.setStyleSheet("font-size: 8pt; color: #7f8c8d;")
        self.quota_bar = QProgressBar()
        self.quota_bar.setFixedHeight(6)
        self.quota_bar.setTextVisible(False)
        self.quota_bar.setStyleSheet("QProgressBar::chunk { background-color: #3498db; }")
        self.quota_bar.hide()
        quota_layout.addWidget(self.quota_label)
        quota_layout.addWidget(self.quota_bar)

        main_layout.addLayout(quota_layout)

        # Pause / Connect button
        self.btn_pause = QPushButton(tr("Conectar / Reconectar"))
        self.btn_pause.clicked.connect(self._toggle_pause)
        main_layout.addWidget(self.btn_pause)

        # Separator line
        self.sep_line = QFrame()
        self.sep_line.setFrameShape(QFrame.HLine)
        self.sep_line.setFrameShadow(QFrame.Sunken)
        self.sep_line.setStyleSheet("background-color: #ecf0f1; max-height: 1px;")
        main_layout.addWidget(self.sep_line)

        # 3. Dynamic Transfer List (Scrollable)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMaximumHeight(180)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 8, 0)
        self.scroll_layout.setSpacing(2)
        self.scroll_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        self.scroll.viewport().installEventFilter(self)

        self.empty_label = QLabel(tr("Nenhum arquivo sendo transferido"))
        self.empty_label.setStyleSheet("color: #bdc3c7; font-style: italic; padding: 20px 0;")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.scroll_layout.insertWidget(0, self.empty_label)

        main_layout.addWidget(self.scroll)

        # 4. Metrics & Chart
        metrics_layout = QHBoxLayout()
        self.speed_title_label = QLabel(tr("Velocidade:"))
        self.speed_value = QLabel("0.00 MB/s")
        self.speed_value.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2980b9;")
        self.eta_title_label = QLabel(tr("Restam:"))
        self.eta_value = QLabel("--:--:--")
        self.eta_value.setStyleSheet("font-size: 10pt; font-weight: bold; color: #e67e22;")
        metrics_layout.addWidget(self.speed_title_label)
        metrics_layout.addWidget(self.speed_value)
        metrics_layout.addStretch()
        metrics_layout.addWidget(self.eta_title_label)
        metrics_layout.addWidget(self.eta_value)
        main_layout.addLayout(metrics_layout)

        self.chart_widget = SpeedChartWidget()
        main_layout.addWidget(self.chart_widget)

        # Footer
        footer_layout = QHBoxLayout()
        self.btn_open = QPushButton(tr("Abrir Pasta Local"))
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_cloud_folder)
        footer_layout.addWidget(self.btn_open, 2)

        self.btn_refresh = QPushButton(tr("Atualizar"))
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setToolTip(tr("Recarregar cache de pastas (vfs/refresh)"))
        self.btn_refresh.clicked.connect(self._refresh_vfs)
        footer_layout.addWidget(self.btn_refresh, 1)

        main_layout.addLayout(footer_layout)

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies Dark or Light theme styling to the popup, transfer items, and speed chart."""
        if theme_pref is not None:
            self._theme_pref = theme_pref
        effective_dark = is_dark_theme_active(self._theme_pref)
        theme_state = (effective_dark, self._theme_pref or "auto", tr("Desconectado"))
        if self._applied_theme_state == theme_state:
            return
        self._applied_theme_state = theme_state
        self.is_dark = effective_dark
        if self.is_dark:
            # Tema Escuro (Dark Mode - combina com Breeze Dark / Adwaita Dark)
            bg = "#202326"
            fg = "#fcfcfc"
            border_col = "#3daee9"
            bar_bg = "#2d3238"
            muted = "#a1a9b1"
            empty_col = "#7f8c8d"
            sep_col = "#333940"
            close_bg = "#2d3238"
            close_fg = "#fcfcfc"
            speed_col = "#5dade2"
            eta_col = "#f39c12"
        else:
            # Tema Claro (Light Mode)
            bg = "#ffffff"
            fg = "#2c3e50"
            border_col = "#dcdde1"
            bar_bg = "#ecf0f1"
            muted = "#7f8c8d"
            empty_col = "#95a5a6"
            sep_col = "#ecf0f1"
            close_bg = "#ecf0f1"
            close_fg = "#7f8c8d"
            speed_col = "#2980b9"
            eta_col = "#e67e22"

        self.setObjectName("StatusPopupRoot")
        self.setStyleSheet(f"""
            QWidget#StatusPopupRoot {{
                background-color: {bg};
                border: 1px solid {border_col};
                border-radius: 10px;
            }}
            QWidget {{
                background-color: {bg};
                color: {fg};
                font-family: 'Sans Serif';
                font-size: 10pt;
            }}
            QLabel {{
                background: transparent;
                color: {fg};
            }}
            QPushButton {{
                background-color: #3498db;
                color: white;
                border: none;
                padding: 8px 12px;
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #2980b9;
            }}
            QPushButton:disabled {{
                background-color: {bar_bg};
                color: {muted};
            }}
            QProgressBar {{
                border: none;
                background-color: {bar_bg};
                height: 6px;
                border-radius: 3px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: #27ae60;
                border-radius: 3px;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
        """)
        self.title_label.setStyleSheet(f"font-size: 11pt; color: {fg};")
        self.quota_label.setStyleSheet(f"font-size: 8pt; color: {muted};")
        self.empty_label.setStyleSheet(f"color: {empty_col}; font-style: italic; padding: 20px 0;")
        self.sep_line.setStyleSheet(f"background-color: {sep_col}; max-height: 1px;")
        self.speed_title_label.setStyleSheet(f"color: {fg};")
        self.eta_title_label.setStyleSheet(f"color: {fg};")
        self.speed_value.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {speed_col};")
        self.eta_value.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {eta_col};")
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {close_bg};
                color: {close_fg};
                border: none;
                border-radius: 11px;
                font-weight: bold;
                font-size: 9pt;
                padding: 0;
            }}
            QPushButton:hover {{
                background: #e74c3c;
                color: white;
            }}
        """)
        menu_bg = "#2980b9" if self.is_dark else "#3498db"
        menu_hover = "#3498db" if self.is_dark else "#2980b9"
        self.btn_menu.setStyleSheet(f"""
            QPushButton {{
                background-color: {menu_bg};
                color: white;
                border: none;
                padding: 2px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 8.5pt;
            }}
            QPushButton:hover {{
                background-color: {menu_hover};
            }}
        """)
        combo_border = "#454a4f" if self.is_dark else "#bdc3c7"
        combo_bg = "#202326" if self.is_dark else "#ffffff"
        combo_fg = "#eff0f1" if self.is_dark else "#2c3e50"
        self.remote_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {combo_border};
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 8.5pt;
                font-weight: bold;
                background-color: {combo_bg};
                color: {combo_fg};
            }}
        """)
        self._apply_pause_button_ui()
        self.chart_widget.is_dark = self.is_dark
        if self.isVisible():
            self.chart_widget.update()
        for i in range(self.scroll_layout.count()):
            w = self.scroll_layout.itemAt(i).widget()
            if isinstance(w, (TransferItem, RecentTransferItem)):
                w.apply_theme(self.is_dark)

    def set_tray_menu(self, menu: QMenu) -> None:
        """Attaches the application context menu to the Menu button."""
        self.btn_menu.setMenu(menu)

    def show_window_mode(self) -> None:
        """Displays and centers the dashboard window on screen."""
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.center().x() - self.width() // 2
            y = geo.center().y() - self.height() // 2
            self.move(max(geo.left(), x), max(geo.top(), y))
        self.show()
        self.raise_()
        self.activateWindow()

    def retranslate_ui(self) -> None:
        """Updates all visible labels and tooltips in the popup when the language changes."""
        self.btn_menu.setText(tr("Menu ▾"))
        if self.window_mode:
            self.setWindowTitle(f"DucksDrive - {tr('Painel de Controle')}")
        self.btn_close.setToolTip(tr("Fechar Painel (ou pressione Esc)"))
        self.lbl_cloud.setText(tr("Nuvem:"))
        self.update_mounted_remotes()
        self._apply_pause_button_ui()
        self.empty_label.setText(tr("Nenhum arquivo sendo transferido"))
        self.speed_title_label.setText(tr("Velocidade:"))
        self.eta_title_label.setText(tr("Restam:"))
        self.btn_open.setText(tr("Abrir Pasta Local"))
        self.btn_refresh.setText(tr("Atualizar"))
        self.btn_refresh.setToolTip(tr("Recarregar cache de pastas (vfs/refresh)"))
        active_daemon = self.get_active_daemon()
        if active_daemon and active_daemon.is_running():
            self.status_badge.setText(tr("● Online"))
            if self._last_quota_values is not None:
                self._on_quota_success(*self._last_quota_values)
            else:
                clean_r = (self.current_remote_name or active_daemon.current_remote or "").rstrip(":")
                if clean_r in _quota_cache:
                    self._on_quota_success(*_quota_cache[clean_r])
                elif self._last_quota_update == 0:
                    self.quota_label.setText(tr("Calculando espaço..."))
                else:
                    self._fetch_quota()
        else:
            self.status_badge.setText(tr("● Offline"))
            if active_daemon and active_daemon.state == "starting":
                self.quota_label.setText(tr("Calculando espaço..."))
            else:
                self.quota_label.setText(tr("Desconectado"))
        if self.isVisible():
            self.chart_widget.update()

    def update_mounted_remotes(self, active_remotes: Optional[list[str]] = None) -> None:
        """Updates the cloud selector combo box with mounted/active remotes."""
        self.remote_combo.blockSignals(True)
        self.remote_combo.clear()

        if active_remotes is not None:
            remotes_to_show = [r.rstrip(":") for r in active_remotes]
        else:
            remotes_to_show = [
                r for r, d in self.daemons.items() if d.is_running() or d.state in ("mounted", "starting")
            ]

        if not remotes_to_show:
            remotes_to_show = list(self.daemons.keys())

        if not remotes_to_show:
            self.remote_combo.addItem(tr("(Nenhuma nuvem conectada)"), "")
            self.remote_combo.setEnabled(False)
        else:
            self.remote_combo.setEnabled(len(remotes_to_show) > 1)
            for r in remotes_to_show:
                d = self.daemons.get(r)
                is_active = d.is_running() if d else False
                badge = f"☁ {r} (" + tr("Ativo") + ")" if is_active else f"☁ {r}"
                self.remote_combo.addItem(badge, r)

            target = self.current_remote_name or remotes_to_show[0]
            idx = self.remote_combo.findData(target)
            if idx >= 0:
                self.remote_combo.setCurrentIndex(idx)
            else:
                self.remote_combo.setCurrentIndex(0)
                self.current_remote_name = remotes_to_show[0]

        self.remote_combo.blockSignals(False)

    def _on_remote_combo_changed(self, index: int) -> None:
        selected = str(self.remote_combo.currentData() or "")
        if selected and selected != self.current_remote_name:
            self.current_remote_name = selected
            d = self.get_active_daemon()
            self._on_daemon_state_changed(d.state if d else "stopped", remote_name=selected)
            if hasattr(self, "_remote_speed_history") and selected in self._remote_speed_history:
                self.chart_widget.speed_history = deque(self._remote_speed_history[selected], maxlen=self.chart_widget.max_points)
                if self.isVisible():
                    self.chart_widget.update()
            if selected in self._remote_stats:
                self._latest_stats = self._remote_stats[selected]
                self._render_stats(self._latest_stats)
            self._fetch_quota()

    def update_stats_for_remote(self, remote_name: str, stats: StatsData) -> None:
        """Updates popup telemetry for a specific remote."""
        clean = remote_name.rstrip(":")
        self._remote_stats[clean] = stats
        if not hasattr(self, "_remote_speed_history"):
            self._remote_speed_history = {}
        if clean not in self._remote_speed_history:
            self._remote_speed_history[clean] = deque([0.0] * self.chart_widget.max_points, maxlen=self.chart_widget.max_points)
        self._remote_speed_history[clean].append(stats.speed_mb_s if stats.is_online else 0.0)

        if not self.current_remote_name or self.current_remote_name == clean:
            self.current_remote_name = clean
            self._latest_stats = stats
            self.chart_widget.add_speed_point(stats.speed_mb_s if stats.is_online else 0.0)
            self._render_stats(stats)

    def _detach_quota_worker(self) -> None:
        """Detaches signals from active quota worker so it never updates UI after remote/state switch."""
        if self._quota_worker is not None:
            w = self._quota_worker
            self._quota_worker = None
            try:
                w.quota_fetched.disconnect(self._on_quota_success)
            except (RuntimeError, TypeError):
                pass
            try:
                w.quota_failed.disconnect(self._on_quota_error)
            except (RuntimeError, TypeError):
                pass
            if w.isRunning():
                self._old_quota_workers.append(w)
                w.finished.connect(lambda worker=w: self._cleanup_old_quota_worker(worker))
            else:
                w.deleteLater()

    def _cleanup_old_quota_worker(self, worker: QuotaWorkerThread) -> None:
        if worker in self._old_quota_workers:
            self._old_quota_workers.remove(worker)
        worker.deleteLater()

    @Slot(str)
    def _on_daemon_state_changed(self, state: str, remote_name: Optional[str] = None) -> None:
        """Resets pause state, quota cache, and popup widgets when unmounting or switching cloud remotes."""
        clean_remote = remote_name.rstrip(":") if remote_name else (self.current_remote_name or "")
        active_daemon = self.get_active_daemon()
        self.update_mounted_remotes()

        if self.current_remote_name and clean_remote and clean_remote != self.current_remote_name:
            return

        cur_remote = self.current_remote_name or (active_daemon.current_remote if active_daemon else None)
        if state in ("stopped", "unmounting", "starting", "error") or cur_remote != self._quota_remote:
            self.is_paused = False
            if active_daemon is not None:
                active_daemon.is_user_paused = False
            self._pause_busy = False
            self._last_quota_update = 0
            self._last_quota_values = None
            self._detach_quota_worker()
            if state in ("stopped", "unmounting", "error"):
                self._quota_remote = None
                self._latest_stats = StatsData(is_online=False, status_text=tr("Drive não conectado"))
                self._render_stats(self._latest_stats)
            elif state == "starting":
                self._quota_remote = None
                self.quota_label.setText(tr("Calculando espaço..."))
                self.quota_bar.setValue(0)
                self.quota_bar.hide()
                self.btn_open.setEnabled(False)
                self._apply_pause_button_ui()
            else:
                self._quota_remote = cur_remote
                self._latest_stats = StatsData(is_online=True, status_text=tr("Sincronizado / Ocioso"))
                self._render_stats(self._latest_stats)
                clean_r = (cur_remote or "").rstrip(":")
                if clean_r in _quota_cache:
                    self._on_quota_success(*_quota_cache[clean_r])
                if self.isVisible():
                    self._fetch_quota()

    def showEvent(self, event: Any) -> None:
        """Refreshes theme and starts quota timer when popup becomes visible."""
        self.apply_theme()
        super().showEvent(event)
        if self._latest_stats is not None:
            self._render_stats(self._latest_stats)
        self.chart_widget.update()
        if not self._quota_timer.isActive():
            self._quota_timer.start()
        now = time.time()
        if now - self._last_quota_update > 600:
            self._fetch_quota()

    def hideEvent(self, event: Any) -> None:
        """Stops quota timer when popup is hidden to save resources."""
        super().hideEvent(event)
        if self._quota_timer.isActive():
            self._quota_timer.stop()

    def _fetch_quota(self) -> None:
        """Spawns the background thread to fetch cloud quota info without blocking UI or competing with active transfers."""
        if not self.daemon.is_running() or not self.daemon.rc_credentials:
            return

        # Defer quota API calls while file transfers are active to avoid pacer contention and context canceled errors
        if self._latest_stats is not None and self._latest_stats.has_active_transfers:
            return

        if hasattr(self, "_quota_worker") and self._quota_worker:
            if self._quota_worker.isRunning():
                return
        self._detach_quota_worker()

        self._quota_remote = self.daemon.current_remote
        self._quota_worker = QuotaWorkerThread(self.daemon.rc_credentials, self.daemon.current_remote or "gdrive", self)
        self._quota_worker.quota_fetched.connect(self._on_quota_success)
        self._quota_worker.quota_failed.connect(self._on_quota_error)
        self._quota_worker.start()
        self._last_quota_update = time.time()

    def _apply_pause_button_ui(self) -> None:
        """Synchronizes the action button label, enabled state, and color with daemon and pause state."""
        disabled_bg = "#2d3238" if self.is_dark else "#ecf0f1"
        disabled_fg = "#a1a9b1" if self.is_dark else "#7f8c8d"

        if self._pause_busy:
            self.btn_pause.setEnabled(False)

        if not self.daemon.is_running():
            if self.daemon.state == "starting":
                self.btn_pause.setText(tr("Conectando..."))
                self.btn_pause.setEnabled(False)
            elif self.daemon.state == "unmounting":
                self.btn_pause.setText(tr("Desconectando..."))
                self.btn_pause.setEnabled(False)
            else:
                self.btn_pause.setText(tr("Conectar / Reconectar"))
                self.btn_pause.setEnabled(True)
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: #3498db;
                    color: white;
                    border: none;
                    padding: 6px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: #2980b9;
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    color: {disabled_fg};
                }}
            """)
            return

        self.btn_pause.setEnabled(True)
        if self.is_paused:
            self.btn_pause.setText(tr("Retomar Transferências"))
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: #27ae60;
                    color: white;
                    border: none;
                    padding: 6px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: #2ecc71;
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    color: {disabled_fg};
                }}
            """)
        else:
            self.btn_pause.setText(tr("Pausar Transferências"))
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: #f39c12;
                    color: white;
                    border: none;
                    padding: 6px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: #e67e22;
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    color: {disabled_fg};
                }}
            """)

    def _reconnect_from_popup(self) -> None:
        """Connects or reconnects the selected or last cloud remote directly from the status panel."""
        if self.daemon.state in ("starting", "unmounting"):
            return
        from rclone_config import list_remote_names
        from rclone_settings import load_config

        self._pause_busy = True
        self.btn_pause.setEnabled(False)

        def _on_remotes(remotes: Any) -> None:
            self._pause_busy = False
            if self.daemon.is_running() or self.daemon.state in ("starting", "unmounting"):
                self._apply_pause_button_ui()
                return
            names = remotes if isinstance(remotes, list) else []
            if not names:
                self._apply_pause_button_ui()
                return
            selected = (self.current_remote_name or "").rstrip(":")
            current = (self.daemon.current_remote or "").rstrip(":")
            last = str(load_config().get("last_remote") or "").rstrip(":")
            if selected and selected in names:
                target = selected
            elif current and current in names:
                target = current
            elif last and last in names:
                target = last
            else:
                target = names[0]
            if self._tray is not None and hasattr(self._tray, "_mount_remote"):
                self._tray._mount_remote(target)
            else:
                self.daemon.start(target)

        self._pause_runner.submit(list_remote_names, callback=_on_remotes)

    def _toggle_pause(self) -> None:
        if not self.daemon.is_running():
            self._reconnect_from_popup()
            return
        if not self.daemon.rc_credentials:
            return

        target_pause = not self.is_paused
        creds = self.daemon.rc_credentials
        self._pause_busy = True
        self.btn_pause.setEnabled(False)

        def do_request(pause_state: bool) -> bool:
            clean_r = (self.current_remote_name or self.daemon.current_remote or "").rstrip(":")
            from rclone_settings import get_remote_config, load_config
            remote_cfg = get_remote_config(clean_r) if clean_r else {}
            original_limit = str(remote_cfg.get("bwlimit") or load_config().get("bwlimit") or "off")
            if pause_state:
                limit_val = "1B:1B"
            else:
                limit_val = original_limit
            return set_rc_bwlimit(creds, limit_val, timeout=5.0)

        def on_done(ok: Any) -> None:
            self._pause_busy = False
            self.btn_pause.setEnabled(True)
            if ok and self.daemon.is_running():
                self.is_paused = target_pause
                self.daemon.is_user_paused = target_pause
            self._apply_pause_button_ui()

        self._pause_runner.submit(do_request, target_pause, callback=on_done)

    @Slot(object, object)
    def _on_quota_success(self, total: int, used: int) -> None:
        if not self.daemon.is_running():
            return
        if self._quota_remote and self.daemon.current_remote != self._quota_remote:
            return
        self._last_quota_values = (total, used)
        clean_remote = (self.daemon.current_remote or "").rstrip(":")
        if clean_remote:
            _quota_cache[clean_remote] = (total, used)
        from rclone_stats import format_bytes

        if total > 0:
            self.quota_bar.show()
            percent = int((used / total) * 100)
            txt = tr("Usando %s de %s (%s%%)") % (format_bytes(used), format_bytes(total), percent)
            self.quota_label.setText(txt)
            self.quota_bar.setValue(percent)
        else:
            self.quota_bar.hide()
            txt = tr("Usado: %s (Total Ilimitado/Desconhecido)") % format_bytes(used)
            self.quota_label.setText(txt)

    @Slot()
    def _on_quota_error(self) -> None:
        if not self.daemon.is_running():
            return
        clean_remote = (self.daemon.current_remote or "").rstrip(":")
        if clean_remote and clean_remote in _quota_cache:
            # Fall back to cached quota instead of overwriting UI with "Quota indisponível"
            cached_total, cached_used = _quota_cache[clean_remote]
            self._on_quota_success(cached_total, cached_used)
            self._last_quota_update = time.time() - 300  # Retry in 5 minutes
            return

        self._last_quota_values = None
        self.quota_label.setText(tr("Quota indisponível"))
        self.quota_bar.hide()
        self._last_quota_update = time.time() - 540  # Retry after 60s (600 - 540)

    def update_stats(self, stats: StatsData) -> None:
        """Updates UI elements and chart with new telemetry data, reusing transfer widgets efficiently."""
        self._latest_stats = stats
        self.chart_widget.add_speed_point(stats.speed_mb_s if stats.is_online else 0.0)
        if not self.isVisible():
            if not stats.is_online:
                self._last_quota_update = 0
            return
        self._render_stats(stats)

    def _render_stats(self, stats: StatsData) -> None:
        """Renders the telemetry snapshot into visible popup widgets."""
        def clear_transfers():
            for i in reversed(range(self.scroll_layout.count())):
                widget = self.scroll_layout.itemAt(i).widget()
                if isinstance(widget, (TransferItem, RecentTransferItem)):
                    self.scroll_layout.removeWidget(widget)
                    widget.deleteLater()
            self.empty_label.show()

        if stats.is_online:
            self.status_badge.setText(tr("● Online"))
            self.status_badge.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 9pt;")
            self.speed_value.setText(stats.speed_str)
            self.eta_value.setText(stats.eta_str)

            clean_remote = (self.daemon.current_remote or "Drive").rstrip(":")
            self.title_label.setText(f"<b>{clean_remote}</b>")

            if self.isVisible() and self._last_quota_update == 0:
                self._fetch_quota()

            if stats.active_transfers:
                self.empty_label.hide()
                # Remove any leftover recent transfer widgets while active transfers are running
                for i in reversed(range(self.scroll_layout.count())):
                    w = self.scroll_layout.itemAt(i).widget()
                    if isinstance(w, RecentTransferItem):
                        self.scroll_layout.removeWidget(w)
                        w.deleteLater()

                existing_widgets: list[TransferItem] = []
                for i in range(self.scroll_layout.count()):
                    w = self.scroll_layout.itemAt(i).widget()
                    if isinstance(w, TransferItem):
                        existing_widgets.append(w)

                for idx, t in enumerate(stats.active_transfers):
                    name = t.get("name", tr("Arquivo"))
                    pct = max(0, min(100, int(t.get("percentage") or 0)))
                    if idx < len(existing_widgets):
                        existing_widgets[idx].update_data(name, pct)
                    else:
                        item = TransferItem(name, pct, is_dark=self.is_dark)
                        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)

                for extra_w in existing_widgets[len(stats.active_transfers):]:
                    self.scroll_layout.removeWidget(extra_w)
                    extra_w.deleteLater()
            else:
                # Remove active transfer widgets
                for i in reversed(range(self.scroll_layout.count())):
                    w = self.scroll_layout.itemAt(i).widget()
                    if isinstance(w, TransferItem):
                        self.scroll_layout.removeWidget(w)
                        w.deleteLater()

                if stats.vfs_queue_count > 0:
                    for i in reversed(range(self.scroll_layout.count())):
                        w = self.scroll_layout.itemAt(i).widget()
                        if isinstance(w, RecentTransferItem):
                            self.scroll_layout.removeWidget(w)
                            w.deleteLater()
                    self.empty_label.setText(tr("Aguardando o envio de %d arquivo(s) para a nuvem...") % stats.vfs_queue_count)
                    self.empty_label.show()
                elif stats.recent_transfers:
                    self.empty_label.setText(tr("Arquivos Recentes"))
                    self.empty_label.show()

                    existing_recent: list[RecentTransferItem] = []
                    for i in range(self.scroll_layout.count()):
                        w = self.scroll_layout.itemAt(i).widget()
                        if isinstance(w, RecentTransferItem):
                            existing_recent.append(w)

                    for idx, r in enumerate(stats.recent_transfers):
                        name = r.get("name", tr("Arquivo"))
                        size_str = format_bytes(int(r.get("size") or 0))
                        if idx < len(existing_recent):
                            existing_recent[idx].update_data(name, size_str)
                        else:
                            item = RecentTransferItem(name, size_str, is_dark=self.is_dark)
                            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)

                    for extra_w in existing_recent[len(stats.recent_transfers):]:
                        self.scroll_layout.removeWidget(extra_w)
                        extra_w.deleteLater()
                else:
                    self.empty_label.setText(tr("Nenhum arquivo sendo transferido"))
                    clear_transfers()
        else:
            self.title_label.setText("<b>DucksDrive</b>")
            self.status_badge.setText(tr("● Offline"))
            self.status_badge.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 9pt;")
            self.speed_value.setText("0.00 MB/s")
            self.eta_value.setText("--:--:--")
            if self.daemon.state == "starting":
                self.quota_label.setText(tr("Calculando espaço..."))
            else:
                self.quota_label.setText(tr("Desconectado"))
            self.quota_bar.setValue(0)
            self.quota_bar.hide()
            self._last_quota_update = 0
            clear_transfers()

        self._apply_pause_button_ui()
        self.btn_open.setEnabled(self.daemon.is_running())
        self.btn_refresh.setEnabled(self.daemon.is_running())

    def _refresh_vfs(self) -> None:
        """Sends POST /vfs/refresh via TaskRunner and provides visual feedback."""
        creds = self.daemon.rc_credentials if self.daemon.is_running() else None
        if creds is None:
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText(tr("Atualizando..."))

        def _done(ok: bool) -> None:
            if self.daemon.is_running():
                self.btn_refresh.setEnabled(True)
                if ok:
                    self.btn_refresh.setText(tr("✓ Atualizado"))
                    QTimer.singleShot(2000, lambda: self.btn_refresh.setText(tr("Atualizar")))
                else:
                    self.btn_refresh.setText(tr("Atualizar"))

        self._pause_runner.submit(rc_vfs_refresh, creds, callback=_done)

    def _open_cloud_folder(self) -> None:
        """Opens the mount point in the file manager using QDesktopServices and closes popup."""
        path = self.daemon.mount_point
        self.close()
        if os.path.exists(path) or is_path_mounted(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception as e:
                logger.error("Failed to open folder with QDesktopServices: %s", e)
        else:
            QMessageBox.warning(None, tr("Pasta Ausente"), tr("O diretório %s não existe.") % path)

    def show_near_cursor(self) -> None:
        """
        Positions the popup nicely near the mouse cursor (unless manually moved by the user).
        """
        self.adjustSize()
        if not self._user_moved:
            cursor_pos = QCursor.pos()
            screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
            screen_geo = screen.availableGeometry()

            x = cursor_pos.x() - self.width() // 2
            y = cursor_pos.y() - self.height() - 15

            if x < screen_geo.left() + 10:
                x = screen_geo.left() + 10
            elif x + self.width() > screen_geo.right() - 10:
                x = screen_geo.right() - self.width() - 10
            if y < screen_geo.top() + 10:
                y = cursor_pos.y() + 20
            y = max(screen_geo.top() + 10, min(y, screen_geo.bottom() - self.height() - 10))

            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def _begin_drag(self, global_pos: QPoint) -> None:
        """Starts an interactive window move via the compositor (Wayland/X11) with manual fallback."""
        self._user_moved = True
        win = self.windowHandle()
        if win is not None and win.startSystemMove():
            self._drag_pos = None
            return
        self._drag_pos = global_pos - self.frameGeometry().topLeft()

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """Allows dragging the window even when clicking inside the scroll area viewport (floating mode)."""
        if not self.window_mode and watched is self.scroll.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._begin_drag(event.globalPosition().toPoint())
                return True
            if event.type() == QEvent.MouseMove and (event.buttons() & Qt.LeftButton) and self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._drag_pos = None
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: Any) -> None:
        """Initiates window dragging when clicking anywhere on the popup background (floating mode only)."""
        if not self.window_mode and event.button() == Qt.LeftButton:
            self._begin_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        """Fallback window dragging for compositors without startSystemMove support."""
        if not self.window_mode and (event.buttons() & Qt.LeftButton) and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        """Ends manual fallback dragging."""
        if not self.window_mode and event.button() == Qt.LeftButton:
            self._drag_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        """Closes popup when Escape key is pressed (floating mode only)."""
        if not self.window_mode and event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def shutdown_workers(self) -> None:
        """Waits for the background quota and pause workers. Call only when the app is exiting."""
        self._pause_runner.wait_all()
        self._detach_quota_worker()
        for worker in list(self._old_quota_workers):
            try:
                if worker.isRunning():
                    worker.wait(2000)
            except RuntimeError:
                pass

    def closeEvent(self, event: Any) -> None:
        """Closing the popup. In window mode without tray, prompts quit confirmation."""
        if self.window_mode and getattr(self, "_tray", None) is not None:
            if not getattr(self._tray, "_is_shutting_down", False):
                event.ignore()
                self._tray._confirm_quit()
                return
        super().closeEvent(event)
