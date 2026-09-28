"""
status_popup.py - Native-like floating status panel for DucksDrive.

Provides a frameless popup window (Qt.Popup) displaying real-time transfer metrics,
progress bars, active file info, a rolling speed history chart, and quick actions.
"""

import logging
import os
from collections import deque
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QSize, QPoint, QUrl
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
    QWidget
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
            painter.drawText(rect, Qt.AlignCenter, "No active transfer speed")
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


class StatusPopup(QWidget):
    """
    Floating status panel acting as a native system tray popup window.
    Behaves as a popup (Qt.Popup) that dismisses on outside clicks.
    """

    def __init__(self, daemon: RcloneDaemon, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.daemon = daemon
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFixedWidth(320)

        self._init_ui()

    def _init_ui(self) -> None:
        """Initializes the popup layout, widgets, and styling."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # Apply clean modern card styling
        self.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                color: #2c3e50;
                font-family: 'Sans Serif';
                font-size: 11pt;
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
        """)

        # Header layout
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel("<b>DucksDrive Status</b>")
        title_label.setStyleSheet("font-size: 12pt; color: #2c3e50;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        self.status_badge = QLabel("● Offline")
        self.status_badge.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 10pt;")
        header_layout.addWidget(self.status_badge)

        main_layout.addLayout(header_layout)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #ecf0f1; max-height: 1px;")
        main_layout.addWidget(line)

        # File & Progress Section
        self.file_label = QLabel("No active file")
        self.file_label.setStyleSheet("color: #7f8c8d; font-size: 10pt;")
        self.file_label.setWordWrap(True)
        main_layout.addWidget(self.file_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Metrics layout (Speed & ETA)
        metrics_layout = QHBoxLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)

        speed_box = QVBoxLayout()
        speed_box.setSpacing(2)
        speed_title = QLabel("SPEED")
        speed_title.setStyleSheet("font-size: 8pt; color: #95a5a6; font-weight: bold;")
        self.speed_value = QLabel("0.00 MB/s")
        self.speed_value.setStyleSheet("font-size: 11pt; font-weight: bold; color: #2980b9;")
        speed_box.addWidget(speed_title)
        speed_box.addWidget(self.speed_value)
        metrics_layout.addLayout(speed_box)

        metrics_layout.addStretch()

        eta_box = QVBoxLayout()
        eta_box.setSpacing(2)
        eta_title = QLabel("ETA")
        eta_title.setStyleSheet("font-size: 8pt; color: #95a5a6; font-weight: bold;")
        self.eta_value = QLabel("--:--:--")
        self.eta_value.setStyleSheet("font-size: 11pt; font-weight: bold; color: #e67e22;")
        eta_box.addWidget(eta_title)
        eta_box.addWidget(self.eta_value)
        metrics_layout.addLayout(eta_box)

        main_layout.addLayout(metrics_layout)

        # Speed chart
        self.chart_widget = SpeedChartWidget()
        main_layout.addWidget(self.chart_widget)

        # Footer Button (Open Cloud Folder)
        self.btn_open = QPushButton("Open Cloud Folder")
        self.btn_open.clicked.connect(self._open_cloud_folder)
        main_layout.addWidget(self.btn_open)

    def update_stats(self, stats: StatsData) -> None:
        """Updates UI elements and chart with new telemetry data."""
        if stats.is_online:
            self.status_badge.setText("● Online")
            self.status_badge.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 10pt;")
            self.speed_value.setText(stats.speed_str)
            self.eta_value.setText(stats.eta_str)

            # Update chart
            self.chart_widget.add_speed_point(stats.speed_mb_s)

            # Active transfers info
            if stats.active_transfers:
                current_transfer = stats.active_transfers[0]
                filename = current_transfer.get("name", "Transferring...")
                percentage = int(current_transfer.get("percentage", 0))
                self.file_label.setText(filename)
                self.progress_bar.setValue(percentage)
                self.progress_bar.setVisible(True)
            else:
                self.file_label.setText("Synchronized / Idle")
                self.progress_bar.setValue(0)
        else:
            self.status_badge.setText("● Offline")
            self.status_badge.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 10pt;")
            self.speed_value.setText("0.00 MB/s")
            self.eta_value.setText("--:--:--")
            self.file_label.setText("Drive not connected")
            self.progress_bar.setValue(0)
            self.chart_widget.add_speed_point(0.0)

        # Enable/disable open folder based on mount state
        is_mounted = self.daemon.is_running()
        self.btn_open.setEnabled(is_mounted)

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
        Positions the popup nicely near the mouse cursor / system tray
        respecting screen boundaries.
        """
        self.adjustSize()
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        x = cursor_pos.x() - self.width() // 2
        y = cursor_pos.y() - self.height() - 15

        # Keep within screen bounds
        if x < screen_geo.left():
            x = screen_geo.left() + 10
        elif x + self.width() > screen_geo.right():
            x = screen_geo.right() - self.width() - 10

        if y < screen_geo.top():
            y = cursor_pos.y() + 20  # Pop below cursor if taskbar is at top

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
