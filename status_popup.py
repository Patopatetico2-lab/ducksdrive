"""
status_popup.py - Native-like floating status panel for DucksDrive.

Provides a frameless popup window (Qt.Popup) displaying real-time transfer metrics,
progress bars, active file info, a rolling speed history chart, and quick actions.
"""

import logging
import os
import time
from collections import deque
from typing import Any, Optional

from i18n import tr
from PySide6.QtCore import Qt, QUrl, QTimer, QThread, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QLinearGradient,
    QPen,
    QBrush,
    QCursor,
    QDesktopServices
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
    QScrollArea
)

from rclone_daemon import RcloneDaemon
from rclone_stats import StatsData

logger = logging.getLogger(__name__)


class SpeedChartWidget(QWidget):
    """Custom lightweight line chart rendering rolling speed history peaks."""

    def __init__(self, max_points: int = 30, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMaximumHeight(80)
        self.setMinimumHeight(60)
        self.max_points = max_points
        self.speed_history: deque[float] = deque([0.0] * max_points, maxlen=max_points)

    def add_speed_point(self, speed_mb_s: float) -> None:
        """Appends a new speed data point and triggers a repaint."""
        self.speed_history.append(speed_mb_s)
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
        painter.setBrush(QColor(245, 247, 250))
        painter.drawRoundedRect(rect, 8, 8)

        if not self.speed_history or max(self.speed_history) == 0:
            # Draw subtle empty state text
            painter.setPen(QColor(150, 150, 150))
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


class QuotaWorkerThread(QThread):
    """Background worker thread to fetch cloud storage quota without blocking UI."""
    quota_fetched = Signal(int, int, float)  # total, used, speed
    quota_failed = Signal()

    def __init__(self, credentials: Any, remote_name: str, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.credentials = credentials
        self.remote_name = remote_name

    def run(self) -> None:
        import json
        import urllib.request
        import base64
        try:
            creds = self.credentials
            auth_str = f"{creds.user}:{creds.password}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {b64_auth}", "Content-Type": "application/json"}
            
            # Request 1: Operations About
            endpoint_about = f"{creds.url}/operations/about"
            fs_arg = self.remote_name if self.remote_name.endswith(":") else f"{self.remote_name}:"
            req_about = urllib.request.Request(
                url=endpoint_about,
                data=json.dumps({"fs": fs_arg}).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            
            with urllib.request.urlopen(req_about, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                total = int(data.get("total") or 0)
                used = int(data.get("used") or 0)

            # Request 2: Core Stats
            speed = 0.0
            try:
                endpoint_stats = f"{creds.url}/core/stats"
                req_stats = urllib.request.Request(
                    url=endpoint_stats,
                    data=json.dumps({}).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req_stats, timeout=5) as response_stats:
                    stats_data = json.loads(response_stats.read().decode("utf-8"))
                    speed = float(stats_data.get("speed") or 0.0)
            except Exception:
                speed = 0.0
                
            if total > 0 or used > 0:
                self.quota_fetched.emit(total, used, speed)
            else:
                self.quota_failed.emit()
        except Exception as e:
            logger.debug("Failed to fetch quota: %s", e)
            self.quota_failed.emit()


class TransferItem(QWidget):
    """Small widget representing a single file transfer in the list."""

    def __init__(self, name: str, percentage: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.file_name = name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self.name_label = QLabel(name)
        self.name_label.setStyleSheet("font-size: 9pt; color: #2c3e50;")
        self.name_label.setWordWrap(False)
        layout.addWidget(self.name_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(percentage)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

    def update_data(self, name: str, percentage: int) -> None:
        self.file_name = name
        self.name_label.setText(name)
        self.progress.setValue(percentage)


class StatusPopup(QWidget):
    """
    Floating status panel acting as a native system tray popup window.
    Uses Qt.Tool to ensure full compatibility with Wayland and X11 compositors.
    """

    def __init__(self, daemon: RcloneDaemon, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.daemon = daemon
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFixedWidth(340)
        self.is_paused = False

        self._last_quota_update = 0
        self._quota_timer = QTimer(self)
        self._quota_timer.setInterval(10 * 60 * 1000)  # 10 minutes cache
        self._quota_timer.timeout.connect(self._fetch_quota)
        # Note: quota timer is started in showEvent and stopped in hideEvent to avoid background polling when hidden

        self._quota_worker: Optional[QuotaWorkerThread] = None

        self._init_ui()

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

        # 1. Header (Account & Status + Close Button)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        self.title_label = QLabel("<b>DucksDrive</b>")
        self.title_label.setStyleSheet("font-size: 11pt; color: #2c3e50;")
        header_layout.addWidget(self.title_label)
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

        main_layout.addLayout(header_layout)

        # 2. Quota Section
        quota_layout = QVBoxLayout()
        quota_layout.setSpacing(2)
        self.quota_label = QLabel(tr("Calculando espaço..."))
        self.quota_label.setStyleSheet("font-size: 8pt; color: #7f8c8d;")
        self.quota_bar = QProgressBar()
        self.quota_bar.setFixedHeight(6)
        self.quota_bar.setTextVisible(False)
        self.quota_bar.setStyleSheet("QProgressBar::chunk { background-color: #3498db; }")
        quota_layout.addWidget(self.quota_label)
        quota_layout.addWidget(self.quota_bar)
        
        # Speed label
        self.speed_label = QLabel(tr("Velocidade: 0 B/s"))
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setStyleSheet("font-size: 8pt; color: #95a5a6; margin-top: 2px;")
        quota_layout.addWidget(self.speed_label)
        
        main_layout.addLayout(quota_layout)
        
        # Pause button
        self.btn_pause = QPushButton(tr("Pausar Transferências"))
        self.btn_pause.setStyleSheet("""
            QPushButton {
                background-color: #f39c12;
                color: white;
                border: none;
                padding: 6px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #e67e22;
            }
        """)
        self.btn_pause.clicked.connect(self._toggle_pause)
        main_layout.addWidget(self.btn_pause)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #ecf0f1; max-height: 1px;")
        main_layout.addWidget(line)

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
        
        self.empty_label = QLabel(tr("Nenhum arquivo sendo transferido"))
        self.empty_label.setStyleSheet("color: #bdc3c7; font-style: italic; padding: 20px 0;")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.scroll_layout.insertWidget(0, self.empty_label)
        
        main_layout.addWidget(self.scroll)

        # 4. Metrics & Chart
        metrics_layout = QHBoxLayout()
        self.speed_value = QLabel("0.00 MB/s")
        self.speed_value.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2980b9;")
        self.eta_value = QLabel("--:--:--")
        self.eta_value.setStyleSheet("font-size: 10pt; font-weight: bold; color: #e67e22;")
        metrics_layout.addWidget(QLabel(tr("Velocidade:")))
        metrics_layout.addWidget(self.speed_value)
        metrics_layout.addStretch()
        metrics_layout.addWidget(QLabel(tr("Restam:")))
        metrics_layout.addWidget(self.eta_value)
        main_layout.addLayout(metrics_layout)

        self.chart_widget = SpeedChartWidget()
        main_layout.addWidget(self.chart_widget)

        # Footer
        self.btn_open = QPushButton(tr("Abrir Pasta Local"))
        self.btn_open.clicked.connect(self._open_cloud_folder)
        main_layout.addWidget(self.btn_open)

    def showEvent(self, event: Any) -> None:
        """Starts quota timer when popup becomes visible."""
        super().showEvent(event)
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
        """Spawns the background thread to fetch cloud quota info without blocking UI."""
        if not self.daemon.is_running() or not self.daemon.rc_credentials:
            return

        if hasattr(self, "_quota_worker") and self._quota_worker:
            if self._quota_worker.isRunning():
                return
            self._quota_worker.deleteLater()

        self._quota_worker = QuotaWorkerThread(self.daemon.rc_credentials, self.daemon.current_remote or "gdrive", self)
        self._quota_worker.quota_fetched.connect(self._on_quota_success)
        self._quota_worker.quota_failed.connect(self._on_quota_error)
        self._quota_worker.start()
        self._last_quota_update = time.time()

    def _toggle_pause(self) -> None:
        import urllib.request
        import json
        import base64
        import threading
        from rclone_settings import load_config
        
        def do_request():
            try:
                creds = self.daemon.rc_credentials
                if not creds: return
                endpoint = f"{creds.url}/core/bwlimit"
                auth_str = f"{creds.user}:{creds.password}"
                b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
                
                original_limit = str(load_config().get("bwlimit", "off"))
                limit_val = "1" if not self.is_paused else original_limit
                
                req = urllib.request.Request(
                    url=endpoint,
                    data=json.dumps({"bwlimit": limit_val}).encode("utf-8"),
                    headers={"Authorization": f"Basic {b64_auth}", "Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
            except Exception as e:
                import logging
                logging.getLogger("ducksdrive").debug("Failed to set bwlimit via RC: %s", e)

        if not self.daemon.is_running(): return
        
        threading.Thread(target=do_request, daemon=True).start()

        if not self.is_paused:
            self.is_paused = True
            self.btn_pause.setText(tr("Retomar Transferências"))
            self.btn_pause.setStyleSheet("""
                QPushButton {
                    background-color: #27ae60;
                    color: white;
                    border: none;
                    padding: 6px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 9pt;
                }
                QPushButton:hover {
                    background-color: #2ecc71;
                }
            """)
        else:
            self.is_paused = False
            self.btn_pause.setText(tr("Pausar Transferências"))
            self.btn_pause.setStyleSheet("""
                QPushButton {
                    background-color: #f39c12;
                    color: white;
                    border: none;
                    padding: 6px;
                    border-radius: 4px;
                    font-weight: bold;
                    font-size: 9pt;
                }
                QPushButton:hover {
                    background-color: #e67e22;
                }
            """)

    @Slot(int, int, float)
    def _on_quota_success(self, total: int, used: int, speed: float) -> None:
        from rclone_stats import format_bytes
        
        # Update Speed Label
        self.speed_label.setText(f"{tr('Velocidade:')} {format_bytes(speed)}/s")
        
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
        self.quota_label.setText(tr("Quota indisponível"))
        self.quota_bar.hide()
        self._last_quota_update = time.time() - 540  # Retry after 60s (600 - 540)

    def update_stats(self, stats: StatsData) -> None:
        """Updates UI elements and chart with new telemetry data, reusing transfer widgets efficiently."""
        def clear_transfers():
            for i in reversed(range(self.scroll_layout.count())):
                widget = self.scroll_layout.itemAt(i).widget()
                if isinstance(widget, TransferItem):
                    self.scroll_layout.removeWidget(widget)
                    widget.deleteLater()
            self.empty_label.show()

        if stats.is_online:
            self.status_badge.setText(tr("● Online"))
            self.status_badge.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 9pt;")
            self.speed_value.setText(stats.speed_str)
            self.eta_value.setText(stats.eta_str)
            self.chart_widget.add_speed_point(stats.speed_mb_s)
            
            clean_remote = (self.daemon.current_remote or "Drive").rstrip(":")
            self.title_label.setText(f"<b>{clean_remote}</b>")

            if self.isVisible() and self._last_quota_update == 0:
                self._fetch_quota()

            if stats.active_transfers:
                self.empty_label.hide()
                # Use a dictionary to match TransferItems by name
                existing_items = {}
                for i in reversed(range(self.scroll_layout.count())):
                    w = self.scroll_layout.itemAt(i).widget()
                    if isinstance(w, TransferItem):
                        existing_items[w.file_name] = w

                active_names = set()
                for t in stats.active_transfers:
                    name = t.get("name", tr("Arquivo"))
                    pct = int(t.get("percentage") or 0)
                    active_names.add(name)
                    
                    if name in existing_items:
                        existing_items[name].update_data(name, pct)
                    else:
                        item = TransferItem(name, pct)
                        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item)
                        existing_items[name] = item

                # Remove items that are no longer active
                for name, w in existing_items.items():
                    if name not in active_names:
                        self.scroll_layout.removeWidget(w)
                        w.deleteLater()
            else:
                clear_transfers()
        else:
            self.status_badge.setText(tr("● Offline"))
            self.status_badge.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 9pt;")
            self.speed_value.setText("0.00 MB/s")
            self.eta_value.setText("--:--:--")
            self.chart_widget.add_speed_point(0.0)
            self._last_quota_update = 0
            clear_transfers()

        self.btn_open.setEnabled(self.daemon.is_running())

    def _open_cloud_folder(self) -> None:
        """Opens the mount point in the file manager using QDesktopServices and closes popup."""
        path = self.daemon.mount_point
        if os.path.exists(path):
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception as e:
                logger.error("Failed to open folder with QDesktopServices: %s", e)
        self.close()

    def show_near_cursor(self) -> None:
        """
        Positions the popup nicely near the mouse cursor.
        """
        self.adjustSize()
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        x = cursor_pos.x() - self.width() // 2
        y = cursor_pos.y() - self.height() - 15

        if x < screen_geo.left(): x = screen_geo.left() + 10
        elif x + self.width() > screen_geo.right(): x = screen_geo.right() - self.width() - 10
        if y < screen_geo.top(): y = cursor_pos.y() + 20

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: Any) -> None:
        """Closes popup when Escape key is pressed."""
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def shutdown_workers(self) -> None:
        """Waits for the background quota worker (HTTP timeout is 4s). Call only when the app is exiting."""
        if self._quota_worker:
            try:
                if self._quota_worker.isRunning():
                    self._quota_worker.wait(4500)
            except RuntimeError:
                pass  # C++ object already deleted

    def closeEvent(self, event: Any) -> None:
        """Closing the popup never blocks the GUI thread; the quota worker finishes on its own."""
        super().closeEvent(event)
