"""
ducksdrive/ui/status_popup.py - Native-like floating status panel for DucksDrive.

Part of DucksDrive: Modern Rclone GUI & Cloud Drive Client for Linux.
Official Website: https://patopatetico2-lab.github.io/ducksdrive/
Source Repository: https://github.com/Patopatetico2-lab/ducksdrive

Provides a frameless popup window (Qt.Tool) displaying real-time transfer metrics,
progress bars, active file info, a rolling speed history chart, and quick actions.
"""

import logging
import os
import time
import weakref
from collections import deque
from typing import Any, Optional

from ducksdrive.i18n import tr
from ducksdrive.resources import get_icon_path
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, Slot
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from ducksdrive.system.filemanager_integration import open_local_folder
from ducksdrive.core.rclone_config import TaskRunner
from ducksdrive.core.rclone_daemon import RcloneDaemon, is_path_mounted
from ducksdrive.core.rclone_settings import QUICK_BANDWIDTH_PRESETS, is_dark_theme_active
from ducksdrive.core.rclone_stats import (
    StatsData,
    format_bytes,
    rc_vfs_forget,
    rc_vfs_refresh,
    set_rc_bwlimit,
)
from ducksdrive.ui.popup_widgets import (
    QuotaWorkerThread,
    RecentTransferItem,
    SpeedChartWidget,
    TransferItem,
    _format_transfer_item_details,
    _quota_cache,
)
from ducksdrive.ui.turbo_transfer_dialog import (
    TurboTransferDialog,
    TurboTransferWorker,  # noqa: F401
)

logger = logging.getLogger(__name__)


def _get_window_icon() -> QIcon:
    """Returns the application icon without importing gui_tray to avoid circular imports."""
    icon_path = get_icon_path()
    if icon_path and os.path.exists(icon_path):
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
        self._tray_ref: Optional[weakref.ref[Any]] = None
        if window_mode:
            super().__init__(parent, Qt.Window)
            self.setWindowTitle(f"DucksDrive - {tr('Painel de Controle')}")
            self.setWindowIcon(_get_window_icon())
            self.setMinimumWidth(376)
        else:
            super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setFixedWidth(364)

        self.daemons: dict[str, RcloneDaemon] = daemons.copy() if daemons else {}
        self._fallback_daemon: Optional[RcloneDaemon] = daemon
        if daemon is not None:
            clean_name = (daemon.current_remote or "default").rstrip(":")
            self.daemons[clean_name] = daemon
        self._connected_daemon_ids: set[int] = set()
        self.current_remote_name: Optional[str] = next(iter(self.daemons.keys())) if self.daemons else None
        self._remote_stats: dict[str, StatsData] = {}
        self._runtime_bwlimit: dict[str, str] = {}

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
        for r, d in list(self.daemons.items()):
            self._connect_daemon_signals(r, d)

    def _connect_daemon_signals(self, remote_name: str, daemon: RcloneDaemon) -> None:
        """Connects daemon state_changed once per daemon instance to avoid duplicate triggers."""
        did = id(daemon)
        if did in self._connected_daemon_ids:
            return
        self._connected_daemon_ids.add(did)
        clean = remote_name.rstrip(":")
        daemon.state_changed.connect(lambda s, rem=clean: self._on_daemon_state_changed(s, rem))

    @property
    def _tray(self) -> Optional[Any]:
        return self._tray_ref() if self._tray_ref is not None else None

    @_tray.setter
    def _tray(self, value: Optional[Any]) -> None:
        self._tray_ref = weakref.ref(value) if value is not None else None

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
        self._connect_daemon_signals(clean, daemon)
        self.update_mounted_remotes()

    def remove_daemon(self, remote_name: str) -> None:
        """Removes a deleted remote's daemon and telemetry caches from the popup."""
        clean = remote_name.rstrip(":")
        removed = self.daemons.pop(clean, None)
        if removed is not None:
            self._connected_daemon_ids.discard(id(removed))
            if self._fallback_daemon is removed:
                self._fallback_daemon = None
        self._remote_stats.pop(clean, None)
        self._runtime_bwlimit.pop(clean, None)
        if hasattr(self, "_remote_speed_history"):
            self._remote_speed_history.pop(clean, None)
        if self.current_remote_name == clean:
            self.current_remote_name = None
        self.update_mounted_remotes()
        if not self.daemons:
            self._latest_stats = StatsData(is_online=False, status_text=tr("Drive não conectado"))
            if self.isVisible():
                self._render_stats(self._latest_stats)
            else:
                self._apply_pause_button_ui()

    def _init_ui(self) -> None:
        """Initializes the popup layout, grouped cards, and action hierarchy."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        # 1. Header (Title left • Status badge, Menu, Close right)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.title_label = QLabel("<b>DucksDrive</b>")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.status_badge = QLabel(tr("● Offline"))
        header_layout.addWidget(self.status_badge)

        self.btn_menu = QPushButton(tr("Menu"))
        self.btn_menu.setFixedHeight(24)
        self.btn_menu.setCursor(Qt.PointingHandCursor)
        header_layout.addWidget(self.btn_menu)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip(tr("Fechar Painel (ou pressione Esc)"))
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)
        if self.window_mode:
            self.btn_close.hide()

        main_layout.addLayout(header_layout)

        # 2. Cloud & Storage Card
        self.cloud_card = QFrame()
        self.cloud_card.setObjectName("SectionCard")
        cloud_card_layout = QVBoxLayout(self.cloud_card)
        cloud_card_layout.setContentsMargins(10, 9, 10, 9)
        cloud_card_layout.setSpacing(7)

        self.selector_bar = QHBoxLayout()
        self.selector_bar.setContentsMargins(0, 0, 0, 0)
        self.selector_bar.setSpacing(8)

        self.lbl_cloud = QLabel(tr("Nuvem:"))
        self.lbl_cloud.setFixedWidth(56)
        self.selector_bar.addWidget(self.lbl_cloud)

        self.remote_combo = QComboBox()
        self.remote_combo.setFixedHeight(26)
        self.remote_combo.currentIndexChanged.connect(self._on_remote_combo_changed)
        self.selector_bar.addWidget(self.remote_combo, 1)

        self.btn_remote_settings = QPushButton("⚙")
        self.btn_remote_settings.setFixedSize(26, 26)
        self.btn_remote_settings.setCursor(Qt.PointingHandCursor)
        self.btn_remote_settings.setToolTip(tr("Configurações da Nuvem..."))
        self.btn_remote_settings.setEnabled(False)
        self.btn_remote_settings.clicked.connect(self._open_current_remote_settings)
        self.selector_bar.addWidget(self.btn_remote_settings)

        cloud_card_layout.addLayout(self.selector_bar)

        quota_layout = QVBoxLayout()
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(4)
        self.quota_label = QLabel(tr("Desconectado"))
        self.quota_bar = QProgressBar()
        self.quota_bar.setRange(0, 1000)
        self.quota_bar.setFixedHeight(5)
        self.quota_bar.setTextVisible(False)
        self.quota_bar.hide()
        quota_layout.addWidget(self.quota_label)
        quota_layout.addWidget(self.quota_bar)
        cloud_card_layout.addLayout(quota_layout)

        main_layout.addWidget(self.cloud_card)

        # Inhibitor Warning Banner (Shutdown Protection)
        self.lbl_inhibitor_banner = QLabel(tr("⚠️ Desligamento Adiado: Gravando arquivos na nuvem"))
        self.lbl_inhibitor_banner.setWordWrap(True)
        self.lbl_inhibitor_banner.setAlignment(Qt.AlignCenter)
        self.lbl_inhibitor_banner.setStyleSheet("""
            QLabel {
                background-color: #d35400;
                color: white;
                font-weight: bold;
                font-size: 8.5pt;
                padding: 6px 8px;
                border-radius: 6px;
            }
        """)
        self.lbl_inhibitor_banner.hide()
        main_layout.addWidget(self.lbl_inhibitor_banner)

        # 3. Network & VFS Cache Controls Card (aligned grid)
        self.controls_card = QFrame()
        self.controls_card.setObjectName("SectionCard")
        controls_grid = QGridLayout(self.controls_card)
        controls_grid.setContentsMargins(10, 9, 10, 9)
        controls_grid.setHorizontalSpacing(8)
        controls_grid.setVerticalSpacing(7)
        controls_grid.setColumnStretch(1, 1)

        self.lbl_bw = QLabel(tr("Banda:"))
        self.lbl_bw.setFixedWidth(56)
        controls_grid.addWidget(self.lbl_bw, 0, 0)

        self.combo_bwlimit = QComboBox()
        self.combo_bwlimit.setFixedHeight(26)
        for label, rate in QUICK_BANDWIDTH_PRESETS:
            self.combo_bwlimit.addItem(tr(label), rate)
        self.combo_bwlimit.currentIndexChanged.connect(self._on_quick_bwlimit_changed)
        controls_grid.addWidget(self.combo_bwlimit, 0, 1, 1, 2)

        self.lbl_vfs_title = QLabel(tr("VFS SSD:"))
        self.lbl_vfs_title.setFixedWidth(56)
        controls_grid.addWidget(self.lbl_vfs_title, 1, 0)

        self.lbl_vfs_info = QLabel(tr("0 B no SSD • 0 na fila"))
        controls_grid.addWidget(self.lbl_vfs_info, 1, 1)

        self.btn_clear_inactive = QPushButton(tr("Limpar Inativo"))
        self.btn_clear_inactive.setToolTip(
            tr("Liberar metadados de pastas inativas da memória RAM (vfs/forget). Para limpar o SSD, use Configurações -> Limpar Cache Agora.")
        )
        self.btn_clear_inactive.setFixedHeight(24)
        self.btn_clear_inactive.setCursor(Qt.PointingHandCursor)
        self.btn_clear_inactive.clicked.connect(self._on_clear_inactive_vfs)
        controls_grid.addWidget(self.btn_clear_inactive, 1, 2)

        self.bw_layout = controls_grid
        self.vfs_layout = controls_grid
        main_layout.addWidget(self.controls_card)

        # Separator line
        self.sep_line = QFrame()
        self.sep_line.setFrameShape(QFrame.HLine)
        self.sep_line.setFrameShadow(QFrame.Sunken)
        self.sep_line.setFixedHeight(1)
        main_layout.addWidget(self.sep_line)

        # 4. Dynamic Transfer List (Scrollable)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(56)
        self.scroll.setMaximumHeight(160)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("ScrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 6, 0)
        self.scroll_layout.setSpacing(2)
        self.scroll_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        self.scroll.viewport().installEventFilter(self)

        self.empty_label = QLabel(tr("Nenhum arquivo sendo transferido"))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.scroll_layout.insertWidget(0, self.empty_label)

        main_layout.addWidget(self.scroll)

        # 5. Metrics & Chart
        metrics_layout = QHBoxLayout()
        metrics_layout.setContentsMargins(2, 0, 2, 0)
        self.speed_title_label = QLabel(tr("Velocidade:"))
        self.speed_value = QLabel("0.00 MB/s")
        self.eta_title_label = QLabel(tr("Restam:"))
        self.eta_value = QLabel("--:--:--")
        metrics_layout.addWidget(self.speed_title_label)
        metrics_layout.addWidget(self.speed_value)
        metrics_layout.addStretch()
        metrics_layout.addWidget(self.eta_title_label)
        metrics_layout.addWidget(self.eta_value)
        main_layout.addLayout(metrics_layout)

        self.chart_widget = SpeedChartWidget()
        main_layout.addWidget(self.chart_widget)

        # 6. Unified Bottom Action Bar (Primary action + aligned secondary controls)
        footer_container = QVBoxLayout()
        footer_container.setContentsMargins(0, 2, 0, 0)
        footer_container.setSpacing(6)

        self.btn_open = QPushButton(tr("Abrir Pasta Local"))
        self.btn_open.setFixedHeight(34)
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_cloud_folder)
        footer_container.addWidget(self.btn_open)

        secondary_actions = QHBoxLayout()
        secondary_actions.setContentsMargins(0, 0, 0, 0)
        secondary_actions.setSpacing(8)

        self.btn_pause = QPushButton(tr("Conectar / Reconectar"))
        self.btn_pause.setFixedHeight(30)
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.clicked.connect(self._toggle_pause)
        secondary_actions.addWidget(self.btn_pause, 3)

        self.btn_refresh = QPushButton(tr("Atualizar"))
        self.btn_refresh.setFixedHeight(30)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setToolTip(tr("Recarregar cache de pastas (vfs/refresh)"))
        self.btn_refresh.clicked.connect(self._refresh_vfs)
        secondary_actions.addWidget(self.btn_refresh, 2)

        self.btn_turbo = QPushButton(tr("⚡ Turbo"))
        self.btn_turbo.setFixedHeight(30)
        self.btn_turbo.setCursor(Qt.PointingHandCursor)
        self.btn_turbo.setEnabled(False)
        self.btn_turbo.setToolTip(
            tr("Transferência Turbo Paralela (baixar ou enviar pastas com múltiplos fluxos simultâneos)")
        )
        self.btn_turbo.clicked.connect(self._open_turbo_transfer)
        secondary_actions.addWidget(self.btn_turbo, 2)

        footer_container.addLayout(secondary_actions)
        main_layout.addLayout(footer_container)

    def _apply_status_badge_ui(self, is_online: bool) -> None:
        """Styles the header status badge pill according to connection state and theme."""
        if is_online:
            self.status_badge.setText(tr("● Online"))
            bg_col = "#1c3829" if self.is_dark else "#e8f8f0"
            fg_col = "#2ecc71" if self.is_dark else "#1e8449"
            bd_col = "#25523b" if self.is_dark else "#b7e4c7"
        else:
            self.status_badge.setText(tr("● Offline"))
            bg_col = "#3b2024" if self.is_dark else "#fdecea"
            fg_col = "#ff6b6b" if self.is_dark else "#c0392b"
            bd_col = "#5c2b30" if self.is_dark else "#f5c6cb"
        self.status_badge.setStyleSheet(
            f"background-color: {bg_col}; color: {fg_col}; border: 1px solid {bd_col}; "
            f"border-radius: 9px; padding: 2px 8px; font-weight: bold; font-size: 8.5pt;"
        )

    def _secondary_button_qss(self, font_size: str = "8.5pt", padding: str = "3px 10px", radius: int = 6) -> str:
        """Returns cohesive QSS for subtle secondary action buttons."""
        sec_bg = "#2d3239" if self.is_dark else "#f1f3f6"
        sec_fg = "#eff0f1" if self.is_dark else "#2c3e50"
        sec_border = "#424852" if self.is_dark else "#cfd6de"
        sec_hover = "#39404a" if self.is_dark else "#e3e7ed"
        dis_bg = "#262a30" if self.is_dark else "#f4f6f8"
        dis_fg = "#6e7681" if self.is_dark else "#9aa5b1"
        return f"""
            QPushButton {{
                background-color: {sec_bg};
                color: {sec_fg};
                border: 1px solid {sec_border};
                border-radius: {radius}px;
                font-weight: bold;
                font-size: {font_size};
                padding: {padding};
            }}
            QPushButton:hover {{
                background-color: {sec_hover};
            }}
            QPushButton:disabled {{
                background-color: {dis_bg};
                color: {dis_fg};
                border-color: transparent;
            }}
        """

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies Dark or Light theme styling to the popup, cards, transfer items, and speed chart."""
        if theme_pref is not None:
            self._theme_pref = theme_pref
        effective_dark = is_dark_theme_active(self._theme_pref)
        theme_state = (effective_dark, self._theme_pref or "auto", tr("Desconectado"))
        if self._applied_theme_state == theme_state:
            return
        self._applied_theme_state = theme_state
        self.is_dark = effective_dark
        if self.is_dark:
            bg = "#1e2125"
            card_bg = "#262a30"
            card_border = "#343a42"
            fg = "#f2f4f7"
            border_col = "#3daee9"
            bar_bg = "#323841"
            muted = "#9ca5b0"
            empty_col = "#7f8c8d"
            sep_col = "#30353d"
            close_bg = "#2d3239"
            close_fg = "#cfd5dc"
            speed_col = "#5dade2"
            eta_col = "#f39c12"
            combo_border = "#454c56"
            combo_bg = "#1e2125"
            combo_fg = "#eff0f1"
        else:
            bg = "#ffffff"
            card_bg = "#f5f7fa"
            card_border = "#e2e6ec"
            fg = "#2c3e50"
            border_col = "#cfd6de"
            bar_bg = "#e4e8ee"
            muted = "#6c7a89"
            empty_col = "#8c9aa8"
            sep_col = "#e6e9ef"
            close_bg = "#eef1f5"
            close_fg = "#5c6b7a"
            speed_col = "#2980b9"
            eta_col = "#e67e22"
            combo_border = "#c5cdd6"
            combo_bg = "#ffffff"
            combo_fg = "#2c3e50"

        self.setObjectName("StatusPopupRoot")
        self.setStyleSheet(f"""
            QWidget#StatusPopupRoot {{
                background-color: {bg};
                border: 1px solid {border_col};
                border-radius: 10px;
            }}
            QWidget#ScrollContent {{
                background: transparent;
            }}
            QFrame#SectionCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
            }}
            QLabel {{
                background: transparent;
                color: {fg};
            }}
            QProgressBar {{
                border: none;
                background-color: {bar_bg};
                height: 5px;
                border-radius: 2px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: #3498db;
                border-radius: 2px;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
        """)
        self.title_label.setStyleSheet(f"font-size: 11.5pt; color: {fg};")
        self.lbl_cloud.setStyleSheet(f"font-size: 8.5pt; font-weight: bold; color: {muted};")
        self.lbl_bw.setStyleSheet(f"font-size: 8.5pt; font-weight: bold; color: {muted};")
        self.lbl_vfs_title.setStyleSheet(f"font-size: 8.5pt; font-weight: bold; color: {muted};")
        self.lbl_vfs_info.setStyleSheet(f"font-size: 8.5pt; font-weight: bold; color: {speed_col};")
        self.quota_label.setStyleSheet(f"font-size: 8pt; color: {muted};")
        self.empty_label.setStyleSheet(f"color: {empty_col}; font-style: italic; padding: 12px 0;")
        self.sep_line.setStyleSheet(f"background-color: {sep_col}; max-height: 1px;")
        self.speed_title_label.setStyleSheet(f"font-size: 9pt; color: {muted};")
        self.eta_title_label.setStyleSheet(f"font-size: 9pt; color: {muted};")
        self.speed_value.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {speed_col};")
        self.eta_value.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {eta_col};")

        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {close_bg};
                color: {close_fg};
                border: 1px solid {card_border};
                border-radius: 12px;
                font-weight: bold;
                font-size: 9pt;
                padding: 0;
            }}
            QPushButton:hover {{
                background: #e74c3c;
                color: white;
                border-color: #e74c3c;
            }}
        """)

        self.btn_menu.setStyleSheet(f"""
            QPushButton {{
                background-color: {close_bg};
                color: {fg};
                border: 1px solid {combo_border};
                padding: 2px 18px 2px 9px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 8.5pt;
            }}
            QPushButton:hover {{
                background-color: {"#39404a" if self.is_dark else "#e3e7ed"};
            }}
            QPushButton::menu-indicator {{
                subcontrol-origin: padding;
                subcontrol-position: right center;
                right: 6px;
            }}
        """)

        combo_qss = f"""
            QComboBox {{
                border: 1px solid {combo_border};
                border-radius: 5px;
                padding: 2px 8px;
                font-size: 8.5pt;
                font-weight: bold;
                background-color: {combo_bg};
                color: {combo_fg};
            }}
            QComboBox:disabled {{
                color: {muted};
            }}
        """
        self.remote_combo.setStyleSheet(combo_qss)
        self.combo_bwlimit.setStyleSheet(combo_qss)

        self.btn_remote_settings.setStyleSheet(self._secondary_button_qss(font_size="10pt", padding="0", radius=5))
        self.btn_clear_inactive.setStyleSheet(self._secondary_button_qss(font_size="8pt", padding="2px 8px", radius=5))
        self.btn_refresh.setStyleSheet(self._secondary_button_qss(font_size="9pt", padding="4px 10px", radius=6))
        self.btn_turbo.setStyleSheet(self._secondary_button_qss(font_size="9pt", padding="4px 8px", radius=6))

        dis_bg = "#2d3238" if self.is_dark else "#e4e8ee"
        self.btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: #2980b9;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 9.5pt;
            }}
            QPushButton:hover {{
                background-color: #3498db;
            }}
            QPushButton:disabled {{
                background-color: {dis_bg};
                color: {muted};
            }}
        """)

        active_daemon = self.get_active_daemon()
        self._apply_status_badge_ui(bool(active_daemon and active_daemon.is_running()))
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
        self.btn_menu.setText(tr("Menu"))
        if self.window_mode:
            self.setWindowTitle(f"DucksDrive - {tr('Painel de Controle')}")
        self.btn_close.setToolTip(tr("Fechar Painel (ou pressione Esc)"))
        self.lbl_cloud.setText(tr("Nuvem:"))
        self.btn_remote_settings.setToolTip(tr("Configurações da Nuvem..."))
        self.lbl_bw.setText(tr("Banda:"))
        current_rate = self.combo_bwlimit.currentData()
        self.combo_bwlimit.blockSignals(True)
        self.combo_bwlimit.clear()
        for label, rate in QUICK_BANDWIDTH_PRESETS:
            self.combo_bwlimit.addItem(tr(label), rate)
        if current_rate is not None:
            idx = self.combo_bwlimit.findData(current_rate)
            if idx >= 0:
                self.combo_bwlimit.setCurrentIndex(idx)
            elif current_rate:
                self.combo_bwlimit.addItem(tr("Personalizado (%s)") % current_rate, current_rate)
                self.combo_bwlimit.setCurrentIndex(self.combo_bwlimit.count() - 1)
        self.combo_bwlimit.blockSignals(False)
        self.lbl_vfs_title.setText(tr("VFS SSD:"))
        self.btn_clear_inactive.setText(tr("Limpar Inativo"))
        self.btn_clear_inactive.setToolTip(
            tr("Liberar metadados de pastas inativas da memória RAM (vfs/forget). Para limpar o SSD, use Configurações -> Limpar Cache Agora.")
        )
        self.update_mounted_remotes()
        self._apply_pause_button_ui()
        self.empty_label.setText(tr("Nenhum arquivo sendo transferido"))
        self.speed_title_label.setText(tr("Velocidade:"))
        self.eta_title_label.setText(tr("Restam:"))
        self.btn_open.setText(tr("Abrir Pasta Local"))
        self.btn_refresh.setText(tr("Atualizar"))
        self.btn_refresh.setToolTip(tr("Recarregar cache de pastas (vfs/refresh)"))
        self.btn_turbo.setText(tr("⚡ Turbo"))
        self.btn_turbo.setToolTip(
            tr("Transferência Turbo Paralela (baixar ou enviar pastas com múltiplos fluxos simultâneos)")
        )
        active_daemon = self.get_active_daemon()
        if active_daemon and active_daemon.is_running():
            self._apply_status_badge_ui(True)
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
            self._apply_status_badge_ui(False)
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
            self.btn_remote_settings.setEnabled(False)
        else:
            self.remote_combo.setEnabled(len(remotes_to_show) > 1)
            self.btn_remote_settings.setEnabled(True)
            for r in remotes_to_show:
                d = self.daemons.get(r)
                is_active = d.is_running() if d else False
                badge = f"● {r} (" + tr("Ativo") + ")" if is_active else r
                self.remote_combo.addItem(badge, r)

            target = self.current_remote_name or remotes_to_show[0]
            idx = self.remote_combo.findData(target)
            if idx >= 0:
                self.remote_combo.setCurrentIndex(idx)
            else:
                self.remote_combo.setCurrentIndex(0)
                self.current_remote_name = remotes_to_show[0]

        self.remote_combo.blockSignals(False)

    def sync_bwlimit_combo(self) -> None:
        """Synchronizes combo_bwlimit with the effective runtime or configured bandwidth limit."""
        from ducksdrive.core.rclone_settings import get_remote_config, load_config

        clean_sel = (self.current_remote_name or "").rstrip(":")
        runtime_bw = self._runtime_bwlimit.get(clean_sel)
        if runtime_bw:
            bw_limit = runtime_bw
        else:
            rem_cfg = get_remote_config(clean_sel) if clean_sel else {}
            bw_limit = str(rem_cfg.get("bwlimit") or load_config().get("bwlimit") or "off")
        self.combo_bwlimit.blockSignals(True)
        idx = self.combo_bwlimit.findData(bw_limit)
        if idx < 0 and bw_limit:
            self.combo_bwlimit.addItem(tr("Personalizado (%s)") % bw_limit, bw_limit)
            idx = self.combo_bwlimit.count() - 1
        self.combo_bwlimit.setCurrentIndex(max(0, idx))
        self.combo_bwlimit.blockSignals(False)

    def _on_remote_combo_changed(self, index: int) -> None:
        selected = str(self.remote_combo.currentData() or "")
        if selected and selected != self.current_remote_name:
            self.current_remote_name = selected
            d = self.get_active_daemon()
            self.is_paused = bool(d and d.is_user_paused)
            self._on_daemon_state_changed(d.state if d else "stopped", remote_name=selected)
            if hasattr(self, "_remote_speed_history") and selected in self._remote_speed_history:
                self.chart_widget.speed_history = deque(self._remote_speed_history[selected], maxlen=self.chart_widget.max_points)
                if self.isVisible():
                    self.chart_widget.update()
            if selected in self._remote_stats:
                self._latest_stats = self._remote_stats[selected]
                if self.isVisible():
                    self._render_stats(self._latest_stats)
            self.sync_bwlimit_combo()
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
            if self.isVisible():
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
        if clean_remote and state in ("stopped", "unmounting", "error"):
            self._runtime_bwlimit.pop(clean_remote, None)
        prev_selected = self.current_remote_name
        self.update_mounted_remotes()
        active_daemon = self.get_active_daemon()

        # If update_mounted_remotes switched selection to another active remote, refresh UI for the new selection
        if self.current_remote_name and self.current_remote_name != prev_selected and clean_remote != self.current_remote_name:
            new_sel = self.current_remote_name
            new_state = active_daemon.state if active_daemon else "stopped"
            if hasattr(self, "_remote_speed_history") and new_sel in self._remote_speed_history:
                self.chart_widget.speed_history = deque(self._remote_speed_history[new_sel], maxlen=self.chart_widget.max_points)
                if self.isVisible():
                    self.chart_widget.update()
            self.sync_bwlimit_combo()
            clean_remote = new_sel
            state = new_state
        elif self.current_remote_name and clean_remote and clean_remote != self.current_remote_name:
            return

        cur_remote = self.current_remote_name or (active_daemon.current_remote if active_daemon else None)
        if state in ("stopped", "unmounting", "starting", "error") or cur_remote != self._quota_remote:
            if state in ("stopped", "unmounting", "starting", "error"):
                self.is_paused = False
                if active_daemon is not None:
                    active_daemon.is_user_paused = False
            else:
                self.is_paused = bool(active_daemon and active_daemon.is_user_paused)
            self._pause_busy = False
            self._last_quota_update = 0
            self._last_quota_values = None
            self._detach_quota_worker()
            if state in ("stopped", "unmounting", "error"):
                self._quota_remote = None
                self._latest_stats = StatsData(is_online=False, status_text=tr("Drive não conectado"))
                if self.isVisible():
                    self._render_stats(self._latest_stats)
                else:
                    self._apply_pause_button_ui()
            elif state == "starting":
                self._quota_remote = None
                self.quota_label.setText(tr("Calculando espaço..."))
                self.quota_bar.setValue(0)
                self.quota_bar.hide()
                self.btn_open.setEnabled(False)
                self._apply_pause_button_ui()
            else:
                self._quota_remote = cur_remote
                if clean_remote in self._remote_stats:
                    self._latest_stats = self._remote_stats[clean_remote]
                else:
                    self._latest_stats = StatsData(is_online=True, status_text=tr("Sincronizado / Ocioso"))
                if self.isVisible():
                    self._render_stats(self._latest_stats)
                else:
                    self._apply_pause_button_ui()
                clean_r = (cur_remote or "").rstrip(":")
                if clean_r in _quota_cache:
                    self._on_quota_success(*_quota_cache[clean_r])
                if self.isVisible():
                    self._fetch_quota()

    def showEvent(self, event: Any) -> None:
        """Refreshes theme and starts quota timer when popup becomes visible."""
        self.apply_theme()
        self.sync_bwlimit_combo()
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
        active_daemon = self.get_active_daemon()
        if not active_daemon or not active_daemon.is_running() or not active_daemon.rc_credentials:
            return

        remote_name = (active_daemon.current_remote or self.current_remote_name or "").rstrip(":")
        if not remote_name:
            return

        # Defer quota API calls while file transfers are active to avoid pacer contention and context canceled errors
        if self._latest_stats is not None and self._latest_stats.has_active_transfers:
            return

        if hasattr(self, "_quota_worker") and self._quota_worker:
            if self._quota_worker.isRunning():
                return
        self._detach_quota_worker()

        self._quota_remote = active_daemon.current_remote
        self._quota_worker = QuotaWorkerThread(active_daemon.rc_credentials, remote_name, self)
        self._quota_worker.quota_fetched.connect(self._on_quota_success)
        self._quota_worker.quota_failed.connect(self._on_quota_error)
        self._quota_worker.start()
        self._last_quota_update = time.time()

    def _apply_pause_button_ui(self) -> None:
        """Synchronizes the pause/reconnect button label, enabled state, and semantic style."""
        disabled_bg = "#262a30" if self.is_dark else "#f4f6f8"
        disabled_fg = "#6e7681" if self.is_dark else "#9aa5b1"

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
                    background-color: #2980b9;
                    color: white;
                    border: none;
                    padding: 4px 10px;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: #3498db;
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
                    padding: 4px 10px;
                    border-radius: 6px;
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
            p_bg = "#3b2f1c" if self.is_dark else "#fef5e7"
            p_fg = "#f5b041" if self.is_dark else "#b9770e"
            p_bd = "#7e5109" if self.is_dark else "#f5cba7"
            p_hv = "#4e3d21" if self.is_dark else "#fdebd0"
            self.btn_pause.setStyleSheet(f"""
                QPushButton {{
                    background-color: {p_bg};
                    color: {p_fg};
                    border: 1px solid {p_bd};
                    padding: 4px 10px;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: {p_hv};
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    color: {disabled_fg};
                    border-color: transparent;
                }}
            """)

    def _reconnect_from_popup(self) -> None:
        """Connects or reconnects the selected or last cloud remote directly from the status panel."""
        if self.daemon.state in ("starting", "unmounting"):
            return
        from ducksdrive.core.rclone_config import list_remote_names
        from ducksdrive.core.rclone_settings import load_config

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
        target_daemon = self.get_active_daemon()
        if not target_daemon or not target_daemon.is_running():
            self._reconnect_from_popup()
            return
        if not target_daemon.rc_credentials:
            return

        target_pause = not self.is_paused
        creds = target_daemon.rc_credentials
        clean_r = (self.current_remote_name or target_daemon.current_remote or "").rstrip(":")
        from ducksdrive.core.rclone_settings import get_remote_config, load_config
        runtime_bw = self._runtime_bwlimit.get(clean_r)
        if runtime_bw:
            original_limit = runtime_bw
        else:
            remote_cfg = get_remote_config(clean_r) if clean_r else {}
            original_limit = str(remote_cfg.get("bwlimit") or load_config().get("bwlimit") or "off")
        limit_val = "1B:1B" if target_pause else original_limit

        self._pause_busy = True
        self.btn_pause.setEnabled(False)

        def on_done(ok: Any) -> None:
            self._pause_busy = False
            self.btn_pause.setEnabled(True)
            if ok and target_daemon.is_running():
                target_daemon.is_user_paused = target_pause
                if self.get_active_daemon() is target_daemon:
                    self.is_paused = target_pause
            self._apply_pause_button_ui()

        self._pause_runner.submit(set_rc_bwlimit, creds, limit_val, 5.0, callback=on_done)

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
        from ducksdrive.core.rclone_stats import format_bytes

        if total > 0:
            self.quota_bar.show()
            pct_float = (used / total) * 100.0
            pct_str = f"{pct_float:.1f}" if 0 < pct_float < 1.0 else str(int(pct_float))
            txt = tr("Usando %s de %s (%s%%)") % (format_bytes(used), format_bytes(total), pct_str)
            self.quota_label.setText(txt)
            bar_val = int((used / total) * 1000)
            if used > 0 and bar_val < 12:
                bar_val = 12
            self.quota_bar.setValue(min(1000, max(0, bar_val)))
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
            self._apply_status_badge_ui(True)
            if getattr(stats, "is_throttled", False):
                self.speed_value.setText(tr("⏳ Aguardando API..."))
            else:
                self.speed_value.setText(stats.speed_str)
            self.eta_value.setText(stats.eta_str)

            clean_remote = (self.daemon.current_remote or "Drive").rstrip(":")
            self.title_label.setText(f"<b>{clean_remote}</b>")

            if self.isVisible() and self._last_quota_update == 0:
                self._fetch_quota()

            self.combo_bwlimit.setEnabled(True)
            self.btn_clear_inactive.setEnabled(True)
            speed_col = "#5dade2" if self.is_dark else "#2980b9"
            self.lbl_vfs_info.setStyleSheet(f"font-size: 8.5pt; font-weight: bold; color: {speed_col};")
            vfs_cache_bytes = getattr(stats, "vfs_disk_cache_bytes", 0)
            cache_str = format_bytes(vfs_cache_bytes)
            q_str = tr("%d na fila") % stats.vfs_queue_count if stats.vfs_queue_count > 0 else tr("0 na fila")
            self.lbl_vfs_info.setText(f"{cache_str} • {q_str}")

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
                    disp_name, pct, detail_str = _format_transfer_item_details(t, tr("Arquivo"))
                    if idx < len(existing_widgets):
                        existing_widgets[idx].update_data(disp_name, pct, detail_str)
                    else:
                        item = TransferItem(disp_name, pct, is_dark=self.is_dark, detail_text=detail_str)
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
            self._apply_status_badge_ui(False)
            self.speed_value.setText("0.00 MB/s")
            self.eta_value.setText("--:--:--")
            if self.daemon.state == "starting":
                self.quota_label.setText(tr("Calculando espaço..."))
            else:
                self.quota_label.setText(tr("Desconectado"))
            self.quota_bar.setValue(0)
            self.quota_bar.hide()
            self._last_quota_update = 0
            self.combo_bwlimit.setEnabled(False)
            self.btn_clear_inactive.setEnabled(False)
            muted_col = "#9ca5b0" if self.is_dark else "#6c7a89"
            self.lbl_vfs_info.setStyleSheet(f"font-size: 8.5pt; color: {muted_col};")
            self.lbl_vfs_info.setText(tr("Desconectado"))
            self.lbl_inhibitor_banner.hide()
            clear_transfers()

        self._apply_pause_button_ui()
        is_run = self.daemon.is_running()
        self.btn_open.setEnabled(is_run)
        self.btn_refresh.setEnabled(is_run)
        self.btn_turbo.setEnabled(is_run)

    def _open_current_remote_settings(self) -> None:
        """Opens per-remote configuration dialog for the currently selected cloud."""
        remote = (self.current_remote_name or str(self.remote_combo.currentData() or "")).rstrip(":")
        if not remote:
            return
        if self._tray is not None and hasattr(self._tray, "_show_remote_settings_dialog"):
            self._tray._show_remote_settings_dialog(remote)
            return
        from ducksdrive.ui.remote_settings_dialog import RemoteSettingsDialog

        dialog = RemoteSettingsDialog(remote, parent=self if self.window_mode else None)
        dialog.exec()

    def _open_turbo_transfer(self) -> None:
        """Opens the non-blocking parallel Turbo Transfer dialog for the active cloud remote."""
        active_d = self.get_active_daemon()
        if active_d is None:
            return
        remote_name = (self.current_remote_name or active_d.current_remote or "").rstrip(":")
        if not remote_name:
            return
        dlg = TurboTransferDialog(
            remote_name=remote_name,
            mount_point=active_d.mount_point,
            daemon=active_d,
            parent=self if self.window_mode else None,
        )
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _refresh_vfs(self) -> None:
        """Sends POST /vfs/refresh via TaskRunner and provides visual feedback."""
        active_daemon = self.get_active_daemon()
        creds = active_daemon.rc_credentials if (active_daemon and active_daemon.is_running()) else None
        if creds is None:
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText(tr("Atualizando..."))

        def _restore_refresh_label() -> None:
            try:
                self.btn_refresh.setText(tr("Atualizar"))
            except (RuntimeError, AttributeError):
                pass

        def _done(ok: bool) -> None:
            active_d = self.get_active_daemon()
            if active_d and active_d.is_running():
                self.btn_refresh.setEnabled(True)
                if ok:
                    self.btn_refresh.setText(tr("✓ Atualizado"))
                    QTimer.singleShot(2000, _restore_refresh_label)
                else:
                    self.btn_refresh.setText(tr("Atualizar"))
            else:
                self.btn_refresh.setText(tr("Atualizar"))
                self.btn_refresh.setEnabled(False)

        self._pause_runner.submit(
            lambda c=creds: rc_vfs_refresh(c, timeout=15.0, recursive=True, async_mode=True),
            callback=_done,
        )

    def _on_quick_bwlimit_changed(self, index: int) -> None:
        """Dynamically applies the chosen bandwidth limit to the active remote daemon via RC core/bwlimit."""
        rate = str(self.combo_bwlimit.currentData() or "off")
        active_daemon = self.get_active_daemon()
        if not active_daemon or not active_daemon.is_running() or not active_daemon.rc_credentials:
            return
        clean_r = (self.current_remote_name or active_daemon.current_remote or "").rstrip(":")
        if clean_r:
            self._runtime_bwlimit[clean_r] = rate
        if self.is_paused or active_daemon.is_user_paused:
            self.is_paused = False
            active_daemon.is_user_paused = False
            self._apply_pause_button_ui()
        creds = active_daemon.rc_credentials
        self._pause_runner.submit(set_rc_bwlimit, creds, rate)

    def _on_clear_inactive_vfs(self) -> None:
        """Sends POST /vfs/forget via RC to free inactive cached directories/files without unmounting."""
        active_daemon = self.get_active_daemon()
        if not active_daemon or not active_daemon.is_running() or not active_daemon.rc_credentials:
            return
        creds = active_daemon.rc_credentials
        self.btn_clear_inactive.setEnabled(False)
        self.btn_clear_inactive.setText(tr("Limpando..."))

        def _restore_clear_label() -> None:
            try:
                self.btn_clear_inactive.setText(tr("Limpar Inativo"))
            except (RuntimeError, AttributeError):
                pass

        def _done(ok: bool) -> None:
            active_d = self.get_active_daemon()
            if active_d and active_d.is_running():
                self.btn_clear_inactive.setEnabled(True)
                if ok:
                    self.btn_clear_inactive.setText(tr("✓ Limpo"))
                    QTimer.singleShot(2000, _restore_clear_label)
                else:
                    self.btn_clear_inactive.setText(tr("Limpar Inativo"))
            else:
                self.btn_clear_inactive.setText(tr("Limpar Inativo"))
                self.btn_clear_inactive.setEnabled(False)

        self._pause_runner.submit(rc_vfs_forget, creds, callback=_done)

    def set_inhibitor_active(self, is_locked: bool, pending_count: int = 0) -> None:
        """Displays or hides the system shutdown delay warning banner."""
        if is_locked and pending_count > 0:
            self.lbl_inhibitor_banner.setText(
                tr("⚠️ Desligamento Adiado: %d arquivo(s) gravando na nuvem") % pending_count
            )
            self.lbl_inhibitor_banner.show()
        else:
            self.lbl_inhibitor_banner.hide()

    def _open_cloud_folder(self) -> None:
        """Opens the mount point in the file manager without blocking on FUSE stat and closes popup."""
        active_d = self.get_active_daemon()
        if active_d is None:
            return
        path = active_d.mount_point
        self.close()
        if is_path_mounted(path) or os.path.exists(path):
            open_local_folder(path)
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
            if screen is not None:
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
        self._pause_runner.wait_all(3000, run_queued=False)
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
            controller = getattr(self._tray, "controller", None)
            shutting_down = bool(
                getattr(controller, "_is_shutting_down", False)
                or getattr(self._tray, "_is_shutting_down", False)
            )
            if not shutting_down:
                event.ignore()
                self._tray._confirm_quit()
                return
        super().closeEvent(event)
