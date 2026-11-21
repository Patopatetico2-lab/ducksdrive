"""
tests/test_ui.py - Tray, StatusPopup, LogViewer, SettingsDialog, and GNOME fallback UI tests.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

from ducksdrive.i18n import tr


# ============================================================================
# 1. Diagnostic Log Viewer Tests
# ============================================================================
class TestLogViewer:
    def test_log_viewer_level_filter(self, qapp):
        from ducksdrive.ui.log_viewer import LogViewerDialog

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as f:
            f.write("2026/11/08 12:00:00 INFO  : Normal information line\n")
            f.write("2026/11/08 12:00:01 NOTICE: Notice warning message\n")
            f.write("2026/11/08 12:00:02 ERROR : Fatal failure occurred in transfer\n")
            tmp_log = f.name

        try:
            viewer = LogViewerDialog(initial_file=tmp_log)
            # Check lines were read
            raw_text = viewer.text_edit.toPlainText()
            assert "Normal information line" in raw_text
            assert "Fatal failure occurred" in raw_text

            # Test ERROR filter
            viewer.level_combo.setCurrentIndex(2)
            filtered_text = viewer.text_edit.toPlainText()
            assert "Fatal failure occurred" in filtered_text
            assert "Normal information line" not in filtered_text

            # Test WARN + ERR filter
            viewer.level_combo.setCurrentIndex(1)
            warn_text = viewer.text_edit.toPlainText()
            assert "Fatal failure occurred" in warn_text
            assert "Notice warning message" in warn_text
            assert "Normal information line" not in warn_text

            # Test search filter
            viewer.level_combo.setCurrentIndex(0)
            viewer.search_edit.setText("information")
            search_text = viewer.text_edit.toPlainText()
            assert "Normal information line" in search_text
            assert "Fatal failure occurred" not in search_text

            viewer.close()
        finally:
            if os.path.exists(tmp_log):
                os.remove(tmp_log)

    def test_log_viewer_notice_filter_and_tail_read(self, qapp):
        from ducksdrive.ui.log_viewer import LogViewerDialog

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as f:
            f.write("2026/11/19 10:00:00 INFO  : Everything ok\n")
            f.write("2026/11/19 10:00:01 NOTICE: Rate limit hit, slowing down\n")
            tmp_log = f.name

        try:
            viewer = LogViewerDialog(initial_file=tmp_log)
            viewer.level_combo.setCurrentIndex(1)  # warn_err
            txt = viewer.text_edit.toPlainText()
            assert "Rate limit hit" in txt
            assert "Everything ok" not in txt
            viewer.close()
        finally:
            if os.path.exists(tmp_log):
                os.remove(tmp_log)


# ============================================================================
# 2. Multi-Mount UI and Controller Tests
# ============================================================================
class TestMultiMountUI:
    def test_tray_icon_daemons_collection(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.ui.gui_tray import RcloneTrayIcon

        d1 = RcloneDaemon(remote_name="cloud1")
        d2 = RcloneDaemon(remote_name="cloud2")

        tray = RcloneTrayIcon(daemon=d1, daemons={"cloud1": d1})
        assert "cloud1" in tray.daemons
        assert tray.daemon is d1

        tray.register_daemon("cloud2", d2)
        assert "cloud2" in tray.daemons
        assert tray.get_or_create_daemon("cloud2") is d2
        assert len(tray.daemons) == 2

    def test_status_popup_remote_selector(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.ui.status_popup import StatusPopup

        d1 = RcloneDaemon(remote_name="gdrive")
        d2 = RcloneDaemon(remote_name="onedrive")

        popup = StatusPopup(daemon=d1, daemons={"gdrive": d1, "onedrive": d2})
        assert popup.remote_combo is not None
        assert popup.remote_combo.count() == 2

        # Selecting onedrive updates active daemon
        idx = popup.remote_combo.findData("onedrive")
        assert idx >= 0
        popup.remote_combo.setCurrentIndex(idx)
        assert popup.active_remote == "onedrive"
        assert popup.get_active_daemon() is d2

    def test_controller_multi_mount_orchestration(self, qapp):
        from ducksdrive.main import RcloneAppController

        controller = RcloneAppController(qapp, tray_available=False)
        d_gdrive = controller.get_or_create_daemon("gdrive")
        d_onedrive = controller.get_or_create_daemon("onedrive")

        assert controller.daemons["gdrive"] is d_gdrive
        assert controller.daemons["onedrive"] is d_onedrive
        assert "gdrive" in controller.tray.daemons
        assert "onedrive" in controller.tray.daemons

        # Clean shutdown test
        controller.shutdown()
        assert controller._is_shutting_down is True

    def test_status_popup_multi_mount_speed_chart(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.core.rclone_stats import StatsData
        from ducksdrive.ui.status_popup import StatusPopup

        d1 = RcloneDaemon(remote_name="gdrive")
        d2 = RcloneDaemon(remote_name="onedrive")
        popup = StatusPopup(daemon=d1, daemons={"gdrive": d1, "onedrive": d2})

        # Telemetry for gdrive
        stats1 = StatsData(is_online=True, speed_mb_s=15.5, status_text="15.5 MB/s")
        popup.update_stats_for_remote("gdrive", stats1)
        assert popup.chart_widget.speed_history[-1] == 15.5
        assert popup._latest_stats is stats1

        # Switch to onedrive
        stats2 = StatsData(is_online=True, speed_mb_s=2.3, status_text="2.3 MB/s")
        popup.update_stats_for_remote("onedrive", stats2)
        idx = popup.remote_combo.findData("onedrive")
        popup.remote_combo.setCurrentIndex(idx)
        assert popup.current_remote_name == "onedrive"
        assert popup._latest_stats is stats2
        assert popup.chart_widget.speed_history[-1] == 2.3

    def test_tray_unmount_all_checks_all_daemons(self, qapp):
        from ducksdrive.core.rclone_daemon import RCCredentials, RcloneDaemon
        from ducksdrive.ui.gui_tray import RcloneTrayIcon

        d1 = RcloneDaemon(remote_name="d1")
        d2 = RcloneDaemon(remote_name="d2")
        d1.rc_credentials = RCCredentials(user="u1", password="p1", host="127.0.0.1", port=5572)
        d2.rc_credentials = RCCredentials(user="u2", password="p2", host="127.0.0.1", port=5573)
        d1.is_running = lambda: True
        d2.is_running = lambda: True

        tray = RcloneTrayIcon(daemon=d1, daemons={"d1": d1, "d2": d2})

        # Mock fetch_vfs_queue: d1 has 0 pending, d2 has 3 pending
        def mock_fetch(creds):
            return 3 if creds.port == 5573 else 0

        with patch("ducksdrive.ui.gui_tray.fetch_vfs_queue", side_effect=mock_fetch):
            with patch("ducksdrive.ui.gui_tray.QMessageBox.warning") as mock_box:
                mock_box.return_value = 0  # No
                tray._unmount_all()
                if tray.runner._current:
                    tray.runner._current.wait(1000)
                qapp.processEvents()
                # Ensure warning was triggered due to d2 pending transfers
                mock_box.assert_called_once()


# ============================================================================
# 3. GNOME Wayland / Fallback Window Mode Tests
# ============================================================================
class TestGnomeFallback:
    def test_window_mode_initialization(self, qapp):
        from ducksdrive.ui.status_popup import StatusPopup

        popup = StatusPopup(daemon=None, window_mode=True)
        assert popup._window_mode is True
        assert tr("Painel de Controle") in popup.windowTitle()

    def test_controller_tray_unavailable_mode(self, qapp):
        from ducksdrive.main import RcloneAppController

        controller = RcloneAppController(qapp, tray_available=False)
        assert controller.tray_available is False
        assert controller.tray.status_popup._window_mode is True
        controller.shutdown()


# ============================================================================
# 4. UI Regressions, Inhibitors, Bandwidth, and Transfer Improvements Tests
# ============================================================================
class TestUIRegressions:
    def test_inhibitor_popup_banner_integration(self, qapp):
        from ducksdrive.ui.status_popup import StatusPopup

        popup = StatusPopup(daemon=None, window_mode=True)
        assert popup.lbl_inhibitor_banner.isHidden()

        # Activate inhibitor
        popup.set_inhibitor_active(True, pending_count=5)
        assert not popup.lbl_inhibitor_banner.isHidden()
        assert "5" in popup.lbl_inhibitor_banner.text()

        # Release inhibitor
        popup.set_inhibitor_active(False, pending_count=0)
        assert popup.lbl_inhibitor_banner.isHidden()

    def test_status_popup_quick_bwlimit_ui(self, qapp):
        from ducksdrive.core.rclone_daemon import RCCredentials, RcloneDaemon
        from ducksdrive.ui.status_popup import StatusPopup

        daemon = RcloneDaemon()
        daemon.rc_credentials = RCCredentials(user="u", password="p", host="127.0.0.1", port=5572)
        daemon.process = MagicMock()
        daemon.process.poll.return_value = None  # running

        popup = StatusPopup(daemon=daemon, window_mode=True)
        idx_5m = popup.combo_bwlimit.findData("5M")
        assert idx_5m >= 0

        with patch("ducksdrive.ui.status_popup.set_rc_bwlimit") as mock_set:
            popup.combo_bwlimit.setCurrentIndex(idx_5m)
            popup._pause_runner.wait_all(2000)
            mock_set.assert_called_with(daemon.rc_credentials, "5M")

    def test_status_popup_clear_inactive_vfs_ui(self, qapp):
        from ducksdrive.core.rclone_daemon import RCCredentials, RcloneDaemon
        from ducksdrive.ui.status_popup import StatusPopup

        daemon = RcloneDaemon()
        daemon.rc_credentials = RCCredentials(user="u", password="p", host="127.0.0.1", port=5572)
        daemon.process = MagicMock()
        daemon.process.poll.return_value = None  # running

        popup = StatusPopup(daemon=daemon, window_mode=True)
        with patch("ducksdrive.ui.status_popup.rc_vfs_forget", return_value=True) as mock_forget:
            popup._on_clear_inactive_vfs()
            popup._pause_runner.wait_all(2000)
            mock_forget.assert_called_with(daemon.rc_credentials)

    def test_daemon_preserves_remote_after_stop_and_popup_preserves_pause(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.ui.status_popup import StatusPopup

        d1 = RcloneDaemon(remote_name="gdrive")
        d2 = RcloneDaemon(remote_name="onedrive")
        d1.state = "mounted"
        d2.state = "mounted"
        d1.is_running = lambda: True
        d2.is_running = lambda: True
        d1.is_user_paused = True
        d2.is_user_paused = False

        popup = StatusPopup(daemon=d1, daemons={"gdrive": d1, "onedrive": d2})
        popup._on_daemon_state_changed("mounted", "gdrive")
        assert popup.is_paused is True

        # Switch to onedrive -> not paused, and gdrive keeps is_user_paused=True
        idx_od = popup.remote_combo.findData("onedrive")
        popup.remote_combo.setCurrentIndex(idx_od)
        assert popup.is_paused is False
        assert d1.is_user_paused is True

        # Switch back to gdrive -> restored pause state
        idx_gd = popup.remote_combo.findData("gdrive")
        popup.remote_combo.setCurrentIndex(idx_gd)
        assert popup.is_paused is True

        # Stop gdrive daemon -> current_remote is preserved
        d1._finish_stop(True)
        assert d1.current_remote == "gdrive"
        assert d1.is_user_paused is False

    def test_no_duplicate_mount_notifications_and_no_tray_logs_action(self, qapp):
        from ducksdrive.main import RcloneAppController

        controller = RcloneAppController(qapp, tray_available=False)
        d = controller.get_or_create_daemon("gdrive")
        d.is_running = lambda: True

        # Tray menu should not have duplicate "Ver Logs" action
        menu_texts = [act.text() for act in controller.tray._menu.actions()]
        assert not any("Logs" in text for text in menu_texts)

        with patch("ducksdrive.ui.gui_tray.notify_mount_success") as mock_notify:
            with patch.object(controller.runner, "submit"):
                d._set_state("mounted")
                qapp.processEvents()
                # Exactly one mount notification across controller + tray
                mock_notify.assert_called_once()

        controller.shutdown()

    def test_sync_complete_notification_reports_file_count(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.core.rclone_stats import StatsData
        from ducksdrive.ui.gui_tray import RcloneTrayIcon

        d = RcloneDaemon(remote_name="gdrive")
        tray = RcloneTrayIcon(daemon=d, daemons={"gdrive": d})

        s_active = StatsData(
            is_online=True,
            transfers_count=0,
            active_transfers=[{"name": "a.txt", "percentage": 50}, {"name": "b.txt", "percentage": 20}],
        )
        tray.update_stats_for_remote("gdrive", s_active)

        s_done = StatsData(is_online=True, transfers_count=2, active_transfers=[])
        with patch("ducksdrive.ui.gui_tray.notify_sync_complete") as mock_sync_done:
            tray.update_stats_for_remote("gdrive", s_done)
            mock_sync_done.assert_called_once()
            assert mock_sync_done.call_args.kwargs.get("file_count") == 2

    def test_settings_dialog_clear_cache_handles_multiple_daemons(self, qapp, monkeypatch):
        from ducksdrive.core.rclone_daemon import RCCredentials, RcloneDaemon
        from ducksdrive.ui import settings_dialog
        from ducksdrive.ui.settings_dialog import SettingsDialog

        monkeypatch.setattr(settings_dialog, "load_config", lambda: {"theme": "auto", "language": "pt_BR"})
        monkeypatch.setattr(settings_dialog, "get_vfs_cache_usage_bytes", lambda: 1024 * 1024)

        d1 = MagicMock(spec=RcloneDaemon)
        d1.current_remote = "gdrive"
        d1.current_read_only = False
        d1.current_extra_args = None
        d1.rc_credentials = RCCredentials(user="u1", password="p1", port=5572)
        d1.is_running.return_value = True
        d1.stop_async.side_effect = lambda on_done=None: on_done() if on_done else None

        d2 = MagicMock(spec=RcloneDaemon)
        d2.current_remote = "onedrive"
        d2.current_read_only = True
        d2.current_extra_args = ["--fast-list"]
        d2.rc_credentials = RCCredentials(user="u2", password="p2", port=5573)
        d2.is_running.return_value = True
        d2.stop_async.side_effect = lambda on_done=None: on_done() if on_done else None

        dlg = SettingsDialog(daemon=d1, daemons={"gdrive": d1, "onedrive": d2})
        while dlg._cache_runner._current is not None:
            dlg._cache_runner._current.wait(1000)
            qapp.processEvents()

        with patch("ducksdrive.ui.settings_dialog.clear_vfs_cache_files", return_value=True) as mock_clear:
            with patch(
                "ducksdrive.ui.settings_dialog.QMessageBox.question",
                return_value=settings_dialog.QMessageBox.Yes,
            ):
                with patch("ducksdrive.ui.settings_dialog.QMessageBox.information"):
                    dlg._on_pre_clear_checked((1024 * 1024, 0))
                    while dlg._cache_runner._current is not None or dlg._clearing_cache:
                        if dlg._cache_runner._current is not None:
                            dlg._cache_runner._current.wait(1000)
                        qapp.processEvents()
                    mock_clear.assert_called_once()
                    d1.stop_async.assert_called_once()
                    d2.stop_async.assert_called_once()
                    d1.start.assert_called_once_with("gdrive", extra_args=None, read_only=False)
                    d2.start.assert_called_once_with("onedrive", extra_args=["--fast-list"], read_only=True)

    def test_settings_dialog_decimal_cache_size_parsing(self, qapp, monkeypatch):
        from ducksdrive.ui import settings_dialog
        from ducksdrive.ui.settings_dialog import SettingsDialog

        monkeypatch.setattr(
            settings_dialog,
            "load_config",
            lambda: {"vfs_cache_max_size": "2.5G", "theme": "auto", "language": "pt_BR"},
        )
        monkeypatch.setattr(settings_dialog, "get_vfs_cache_usage_bytes", lambda: 0)

        dlg = SettingsDialog()
        dlg._cache_runner.wait_all(1000)
        assert dlg.cache_spin.value() in (2, 3)  # Must never parse "2.5G" as 25 GB
        dlg.close()

    def test_multi_mount_tray_header_resets_after_sync_and_no_duplicate_signals(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon
        from ducksdrive.core.rclone_stats import StatsData
        from ducksdrive.ui.gui_tray import RcloneTrayIcon

        d1 = RcloneDaemon(remote_name="gdrive")
        d2 = RcloneDaemon(remote_name="onedrive")
        d1.state = "mounted"
        d2.state = "mounted"
        d1.is_running = lambda: True
        d2.is_running = lambda: True

        tray = RcloneTrayIcon(daemon=d1, daemons={"gdrive": d1, "onedrive": d2})

        # Ensure state_changed is only connected once per daemon
        calls = []
        orig_handler = tray._on_state_changed

        def _spy(state, rem=None):
            calls.append((state, rem))
            return orig_handler(state, rem)

        tray._on_state_changed = _spy
        d1._set_state("mounted")
        qapp.processEvents()
        assert len(calls) == 1

        # Multi-mount syncing then idle restores "2 Nuvens Conectadas"
        tray.update_stats_for_remote(
            "gdrive",
            StatsData(is_online=True, transfers_count=5, active_transfers=[{"name": "a.bin", "percentage": 50}]),
        )
        assert "Sincronizando" in tray.action_status.text()

        with patch("ducksdrive.ui.gui_tray.notify_sync_complete") as mock_done:
            tray.update_stats_for_remote(
                "gdrive",
                StatsData(is_online=True, transfers_count=6, active_transfers=[]),
            )
            mock_done.assert_called_once()
            assert mock_done.call_args.kwargs.get("file_count") == 1
        assert "2" in tray.action_status.text()
        assert "Sincronizando" not in tray.action_status.text()

    def test_instant_transfer_speed_and_item_details_formatting(self, qapp):
        from ducksdrive.core import rclone_stats
        from ducksdrive.core.rclone_daemon import RCCredentials
        from ducksdrive.ui.status_popup import StatusPopup, _format_transfer_item_details

        creds = RCCredentials(user="u", password="p", host="127.0.0.1", port=5572)
        rc_payload = {
            "speed": 100000.0,  # stale historical session average
            "bytes": 524288000,
            "transfers": 3,
            "errors": 0,
            "eta": 90,
            "transferring": [
                {
                    "name": "video_4k.mp4",
                    "size": 1073741824,
                    "bytes": 536870912,
                    "percentage": 50,
                    "speed": 6291456.0,  # 6.00 MB/s instantaneous
                    "direction": "download",
                }
            ],
        }
        with patch.object(rclone_stats, "rc_request", return_value=(200, rc_payload)):
            st = rclone_stats.fetch_rclone_stats(creds)
            assert st.is_online is True
            assert round(st.speed_mb_s, 2) == 6.00

        disp_name, pct, detail = _format_transfer_item_details(rc_payload["transferring"][0], "Arquivo")
        assert disp_name.startswith("↓ ")
        assert pct == 50
        assert "512.00 MB / 1.00 GB" in detail
        assert "6.00 MB/s" in detail

        # Verify throttled state display in StatusPopup
        popup = StatusPopup(daemon=None, window_mode=True)
        st.is_throttled = True
        popup._render_stats(st)
        assert "API" in popup.speed_value.text()

    def test_asymmetric_bandwidth_and_turbo_transfer_endpoints(self, qapp, tmp_path):
        from ducksdrive.ui.status_popup import StatusPopup, TurboTransferDialog

        popup = StatusPopup(daemon=None, window_mode=True)
        assert popup.combo_bwlimit.findData("4M:off") >= 0

        mount_root = tmp_path / "CloudDrives" / "drive"
        sub = mount_root / "Projetos" / "2026"
        sub.mkdir(parents=True)
        local_dst = tmp_path / "Downloads" / "Backup"

        dlg = TurboTransferDialog(remote_name="drive", mount_point=str(mount_root))
        dlg.cloud_path_edit.setText(str(sub))
        dlg.local_path_edit.setText(str(local_dst))
        resolved = dlg._resolve_endpoints()
        assert resolved is not None
        src, dst, mode = resolved
        assert mode == "download"
        assert src == "drive:Projetos/2026"
        assert dst == str(local_dst)
        dlg.close()
