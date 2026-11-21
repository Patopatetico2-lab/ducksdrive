"""
ducksdrive/ui/turbo_transfer_dialog.py - Parallel Turbo Transfer worker and dialog for DucksDrive.
"""

import os
import subprocess
from collections import deque
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ducksdrive.core.rclone_config import TaskRunner, get_clean_env, get_rclone_path
from ducksdrive.core.rclone_daemon import RcloneDaemon
from ducksdrive.core.rclone_settings import apply_app_theme, load_config
from ducksdrive.core.rclone_stats import rc_vfs_refresh
from ducksdrive.i18n import tr


class TurboTransferWorker(QThread):
    """Background worker executing parallel `rclone copy` outside FUSE single-stream bottlenecks."""

    progress_line = Signal(str)
    finished_transfer = Signal(bool, str)

    def __init__(
        self,
        source: str,
        destination: str,
        transfers: int = 8,
        multi_thread_streams: int = 4,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.source = source
        self.destination = destination
        self.transfers = max(1, min(16, int(transfers)))
        self.multi_thread_streams = max(1, min(8, int(multi_thread_streams)))
        self._process: Optional[subprocess.Popen[str]] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            rclone_bin = get_rclone_path()
        except FileNotFoundError as exc:
            self.finished_transfer.emit(False, tr(str(exc)))
            return

        checkers = max(8, self.transfers * 2)
        cmd = [
            rclone_bin,
            "copy",
            self.source,
            self.destination,
            "--transfers",
            str(self.transfers),
            "--checkers",
            str(checkers),
            "--multi-thread-streams",
            str(self.multi_thread_streams),
            "--fast-list",
            "--stats",
            "1s",
            "--stats-one-line",
            "-v",
        ]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=get_clean_env(),
            )
            last_lines: deque[str] = deque(maxlen=8)
            if self._process.stdout is not None:
                for raw_line in self._process.stdout:
                    if self._cancelled:
                        break
                    line = raw_line.strip()
                    if line:
                        last_lines.append(line)
                        self.progress_line.emit(line)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

            if self._cancelled:
                self.finished_transfer.emit(False, tr("Transferência Turbo cancelada pelo usuário."))
                return
            if self._process.returncode == 0:
                self.finished_transfer.emit(True, tr("Transferência Turbo concluída com sucesso!"))
            else:
                summary = "\n".join(last_lines) or (tr("Código de erro: %d") % self._process.returncode)
                self.finished_transfer.emit(False, summary)
        except Exception as exc:
            self.finished_transfer.emit(False, str(exc))


class TurboTransferDialog(QDialog):
    """
    Dialog for high-speed parallel folder/file copy (`rclone copy --transfers N --multi-thread-streams 4`),
    bypassing FUSE sequential single-file overhead (up to 5.5x faster for many small files).
    """

    def __init__(
        self,
        remote_name: str,
        mount_point: str,
        daemon: Optional[RcloneDaemon] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.remote_name = remote_name.rstrip(":")
        self.mount_point = os.path.abspath(os.path.expanduser(mount_point))
        self.daemon = daemon
        self._worker: Optional[TurboTransferWorker] = None

        self.setWindowTitle(tr("⚡ Transferência Turbo Paralela (%s)") % self.remote_name)
        self.setMinimumWidth(520)
        self._init_ui()
        self.apply_theme(str(load_config().get("theme") or "auto"))

    def apply_theme(self, theme_pref: Optional[str] = None) -> None:
        """Applies the active theme palette to the Turbo Transfer dialog."""
        active_pref = theme_pref or str(load_config().get("theme") or "auto")
        palette = apply_app_theme(active_pref)
        if palette is not None:
            self.setPalette(palette)
            for child in self.findChildren(QWidget):
                child.setPalette(palette)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        info_lbl = QLabel(
            tr(
                "Transfere pastas diretamente com múltiplos fluxos paralelos (--transfers + --multi-thread-streams), "
                "ideal para lotes com centenas de arquivos ou arquivos gigantes sem o gargalo sequencial do FUSE."
            )
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("font-size: 8.5pt; color: #7f8c8d;")
        layout.addWidget(info_lbl)

        form = QFormLayout()
        form.setSpacing(8)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("↓ Baixar da Nuvem para o PC (Download Paralelo)"), "download")
        self.mode_combo.addItem(tr("↑ Enviar do PC para a Nuvem (Upload Paralelo)"), "upload")
        form.addRow(tr("Direção:"), self.mode_combo)

        cloud_row = QHBoxLayout()
        self.cloud_path_edit = QLineEdit()
        self.cloud_path_edit.setPlaceholderText(tr("Subpasta na nuvem (vazio = raiz de %s:)") % self.remote_name)
        self.btn_browse_cloud = QPushButton(tr("Procurar..."))
        self.btn_browse_cloud.clicked.connect(self._browse_cloud_path)
        cloud_row.addWidget(self.cloud_path_edit, 1)
        cloud_row.addWidget(self.btn_browse_cloud)
        form.addRow(tr("Pasta na Nuvem:"), cloud_row)

        local_row = QHBoxLayout()
        self.local_path_edit = QLineEdit()
        self.local_path_edit.setPlaceholderText(os.path.expanduser("~/Downloads"))
        self.btn_browse_local = QPushButton(tr("Procurar..."))
        self.btn_browse_local.clicked.connect(self._browse_local_path)
        local_row.addWidget(self.local_path_edit, 1)
        local_row.addWidget(self.btn_browse_local)
        form.addRow(tr("Pasta no PC:"), local_row)

        self.transfers_spin = QSpinBox()
        self.transfers_spin.setRange(2, 16)
        self.transfers_spin.setValue(8)
        form.addRow(tr("Arquivos Simultâneos:"), self.transfers_spin)
        layout.addLayout(form)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel(tr("Pronto para iniciar."))
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 8.5pt; font-weight: bold; color: #2980b9;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_close = QPushButton(tr("Fechar"))
        self.btn_close.clicked.connect(self._on_close_or_cancel)
        self.btn_start = QPushButton(tr("Iniciar Transferência Turbo"))
        self.btn_start.setStyleSheet(
            "background-color: #2980b9; color: white; border: none; padding: 6px 14px; border-radius: 5px; font-weight: bold;"
        )
        self.btn_start.clicked.connect(self._start_transfer)
        btn_row.addWidget(self.btn_close)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

    def _browse_cloud_path(self) -> None:
        start_dir = self.mount_point if os.path.isdir(self.mount_point) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, tr("Selecionar Pasta na Nuvem"), start_dir)
        if not chosen:
            return
        abs_chosen = os.path.abspath(chosen)
        if abs_chosen == self.mount_point:
            self.cloud_path_edit.setText("")
        elif abs_chosen.startswith(self.mount_point + os.sep):
            rel = os.path.relpath(abs_chosen, self.mount_point)
            self.cloud_path_edit.setText(rel)
        else:
            self.cloud_path_edit.setText(chosen)

    def _browse_local_path(self) -> None:
        start_dir = self.local_path_edit.text().strip() or os.path.expanduser("~/Downloads")
        chosen = QFileDialog.getExistingDirectory(self, tr("Selecionar Pasta no PC"), start_dir)
        if chosen:
            self.local_path_edit.setText(chosen)

    def _resolve_endpoints(self) -> Optional[tuple[str, str, str]]:
        mode = str(self.mode_combo.currentData() or "download")
        local_dir = os.path.abspath(os.path.expanduser(self.local_path_edit.text().strip()))
        if not self.local_path_edit.text().strip():
            QMessageBox.warning(self, tr("Pasta Local Ausente"), tr("Selecione uma pasta local no seu PC."))
            return None
        raw_cloud = self.cloud_path_edit.text().strip()
        if raw_cloud == self.mount_point or raw_cloud.startswith(self.mount_point + os.sep):
            rel = os.path.relpath(raw_cloud, self.mount_point)
            raw_cloud = "" if rel == "." else rel
        else:
            raw_cloud = raw_cloud.lstrip("/")
        remote_spec = f"{self.remote_name}:{raw_cloud}" if raw_cloud else f"{self.remote_name}:"
        if mode == "download":
            os.makedirs(local_dir, exist_ok=True)
            return remote_spec, local_dir, mode
        if not os.path.isdir(local_dir):
            QMessageBox.warning(self, tr("Pasta Ausente"), tr("O diretório %s não existe.") % local_dir)
            return None
        return local_dir, remote_spec, mode

    def _start_transfer(self) -> None:
        resolved = self._resolve_endpoints()
        if not resolved:
            return
        src, dst, mode = resolved
        self.btn_start.setEnabled(False)
        self.btn_close.setText(tr("Cancelar"))
        self.progress_bar.show()
        self.status_label.setText(tr("Iniciando transferência paralela..."))

        worker = TurboTransferWorker(src, dst, transfers=self.transfers_spin.value(), parent=self)
        self._worker = worker
        worker.progress_line.connect(self.status_label.setText)
        worker.finished_transfer.connect(lambda ok, msg, m=mode: self._on_finished(ok, msg, m))
        worker.start()

    def _on_finished(self, ok: bool, msg: str, mode: str) -> None:
        self.progress_bar.hide()
        self.btn_start.setEnabled(True)
        self.btn_close.setText(tr("Fechar"))
        self.status_label.setText(msg)
        if ok and mode == "upload" and self.daemon and self.daemon.is_running() and self.daemon.rc_credentials:
            creds = self.daemon.rc_credentials
            threading_runner = TaskRunner(self)
            threading_runner.submit(lambda c=creds: rc_vfs_refresh(c, recursive=True, async_mode=True))

    def _on_close_or_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            return
        self.close()
