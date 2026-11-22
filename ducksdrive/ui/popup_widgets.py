"""
ducksdrive/ui/popup_widgets.py - Reusable telemetry widgets, speed chart, and quota worker for StatusPopup.
"""

import logging
import time
from collections import deque
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from ducksdrive.core.rclone_stats import format_bytes, rc_request
from ducksdrive.i18n import tr

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # Background card styling
        painter.setPen(Qt.PenStyle.NoPen)
        bg_color = QColor(38, 46, 56) if self.is_dark else QColor(245, 247, 250)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, 8, 8)

        if not self.speed_history or max(self.speed_history) == 0:
            # Draw subtle empty state text
            painter.setPen(QColor(149, 165, 166) if self.is_dark else QColor(150, 150, 150))
            painter.setFont(QFont("Sans Serif", 9))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("Sem velocidade de transferência ativa"))
            painter.end()
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
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(fill_path)

            # Draw speed curve line
            pen = QPen(QColor(41, 128, 185), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
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
        try:
            creds = self.credentials
            fs_arg = self.remote_name if self.remote_name.endswith(":") else f"{self.remote_name}:"

            # Up to 2 attempts with a generous timeout so rate-limited remotes don't hit context canceled
            for attempt in range(2):
                try:
                    status, data = rc_request(creds, "/operations/about", {"fs": fs_arg}, timeout=15.0)
                    if status == 200 and isinstance(data, dict):
                        total = int(data.get("total") or 0)
                        used = int(data.get("used") or 0)
                        if (data.get("total") is not None or data.get("used") is not None) and (total >= 0 and used >= 0):
                            self.quota_fetched.emit(total, used)
                            return
                        break
                except Exception as attempt_err:
                    logger.debug("Quota attempt %d/2 failed: %s", attempt + 1, attempt_err)
                if attempt < 1:
                    time.sleep(2.0)

            self.quota_failed.emit()
        except Exception as e:
            logger.debug("Failed to fetch quota: %s", e)
            self.quota_failed.emit()


def _format_transfer_item_details(t: dict[str, Any], default_name: str) -> tuple[str, int, str]:
    """Formats (display_name_with_arrow, percentage, detail_text) for an active transfer item."""
    raw_name = str(t.get("name") or default_name)
    direction = str(t.get("direction") or "")
    if direction == "upload":
        prefix = "↑ "
    elif direction == "download":
        prefix = "↓ "
    elif direction == "sync":
        prefix = "⇄ "
    else:
        prefix = ""
    display_name = f"{prefix}{raw_name}"
    pct = max(0, min(100, int(t.get("percentage") or 0)))

    size = int(t.get("size") or 0)
    done_bytes = int(t.get("bytes") or 0)
    raw_speed: Any = t.get("speed")
    if raw_speed is None:
        raw_speed = t.get("speedAvg")
    item_speed = float(raw_speed) if isinstance(raw_speed, (int, float)) else 0.0

    parts: list[str] = []
    if size > 0:
        parts.append(f"{format_bytes(done_bytes)} / {format_bytes(size)}")
    if item_speed > 0:
        parts.append(f"{format_bytes(int(item_speed))}/s")
    parts.append(f"{pct}%")
    return display_name, pct, " • ".join(parts)


class TransferItem(QWidget):
    """Small widget representing a single file transfer in the list with direction, size, and speed."""

    def __init__(
        self,
        name: str,
        percentage: int,
        is_dark: bool = False,
        parent: Optional[QWidget] = None,
        detail_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.file_name = name
        self._percentage = percentage
        self._detail_text = detail_text or f"{percentage}%"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.name_label = QLabel(name)
        self.name_label.setWordWrap(False)
        self.name_label.setToolTip(name)
        self.detail_label = QLabel(self._detail_text)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top_row.addWidget(self.name_label, 1)
        top_row.addWidget(self.detail_label, 0)
        layout.addLayout(top_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(percentage)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.apply_theme(is_dark)

    def apply_theme(self, is_dark: bool) -> None:
        text_col = "#ecf0f1" if is_dark else "#2c3e50"
        detail_col = "#95a5a6" if is_dark else "#7f8c8d"
        self.name_label.setStyleSheet(f"font-size: 9pt; color: {text_col};")
        self.detail_label.setStyleSheet(f"font-size: 8pt; color: {detail_col};")

    def update_data(self, name: str, percentage: int, detail_text: Optional[str] = None) -> None:
        if name != self.file_name:
            self.file_name = name
            self.name_label.setText(name)
            self.name_label.setToolTip(name)
        if percentage != self._percentage:
            self._percentage = percentage
            self.progress.setValue(percentage)
        new_detail = detail_text if detail_text is not None else f"{percentage}%"
        if new_detail != self._detail_text:
            self._detail_text = new_detail
            self.detail_label.setText(new_detail)


class RecentTransferItem(QWidget):
    """Small widget representing a completed file transfer in recent history."""

    clicked = Signal(str)

    def __init__(self, name: str, size_str: str, is_dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._file_name = name
        self._size_str = size_str
        self.setObjectName("RecentTransferItem")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Clique para abrir '%s' no gerenciador de arquivos") % name)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(6)

        self.name_label = QLabel(f"✓  {name}")
        self.name_label.setWordWrap(False)
        self.size_label = QLabel(size_str)
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.size_label, 0)
        self.apply_theme(is_dark)

    def apply_theme(self, is_dark: bool) -> None:
        name_col = "#ecf0f1" if is_dark else "#2c3e50"
        size_col = "#95a5a6" if is_dark else "#7f8c8d"
        hover_bg = "rgba(255, 255, 255, 0.06)" if is_dark else "rgba(52, 152, 219, 0.10)"
        self.setStyleSheet(
            f"QWidget#RecentTransferItem {{ border-radius: 4px; }}"
            f" QWidget#RecentTransferItem:hover {{ background-color: {hover_bg}; }}"
        )
        self.name_label.setStyleSheet(f"font-size: 8.5pt; color: {name_col};")
        self.size_label.setStyleSheet(f"font-size: 8pt; color: {size_col};")

    def update_data(self, name: str, size_str: str) -> None:
        if name != self._file_name:
            self._file_name = name
            self.name_label.setText(f"✓  {name}")
            self.setToolTip(tr("Clique para abrir '%s' no gerenciador de arquivos") % name)
        if size_str != self._size_str:
            self._size_str = size_str
            self.size_label.setText(size_str)

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._file_name)
            event.accept()
            return
        super().mouseReleaseEvent(event)
