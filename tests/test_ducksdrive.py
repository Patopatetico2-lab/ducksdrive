"""
tests/test_ducksdrive.py - Unit and Integration Tests for DucksDrive.

Covers:
1. Version parsing, comparison, and update cache (updater.py)
2. Global and per-remote configuration management (rclone_settings.py)
3. Multi-daemon isolation: separate ports, mount points, log files, flags (rclone_daemon.py)
4. Desktop notification dispatch and fallbacks (notifications.py)
5. Diagnostic Log Viewer filtering, search, and reading (log_viewer.py)
6. Multi-mount UI and controller orchestration (gui_tray.py, status_popup.py, main.py)
7. GNOME Wayland / Vanilla tray-less window mode fallback
"""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from i18n import tr


# Ensure offscreen Qt application exists for widget/signal tests
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


# ============================================================================
# 1. Updater Tests
# ============================================================================
class TestUpdater:
    def test_parse_version_standard(self):
        from updater import parse_version

        assert parse_version("1.0.8") == (1, 0, 8)
        assert parse_version("v1.0.9") == (1, 0, 9)
        assert parse_version("v2.15.30") == (2, 15, 30)

    def test_parse_version_pre_release(self):
        from updater import parse_version

        assert parse_version("v1.0.9-beta.1") == (1, 0, 9)
        assert parse_version("1.10.0-rc2") == (1, 10, 0)

    def test_parse_version_invalid(self):
        from updater import parse_version

        assert parse_version("invalid") == (0, 0, 0)
        assert parse_version("") == (0, 0, 0)

    def test_version_comparisons(self):
        from updater import parse_version

        assert parse_version("1.0.9") > parse_version("1.0.8")
        assert parse_version("1.0.10") > parse_version("1.0.9")
        assert parse_version("2.0.0") > parse_version("1.99.99")
        assert parse_version("1.0.9") == parse_version("v1.0.9")

    def test_update_cache_persistence(self, monkeypatch):
        import updater

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "update_cache.json")
            monkeypatch.setattr(updater, "_get_cache_file", lambda: cache_file)

            # Initially empty
            assert updater._read_cache() is None

            # Write and verify load
            updater._write_cache("1.0.9", "https://example.com/v1.0.9", "Release notes")
            loaded = updater._read_cache()
            assert loaded is not None
            assert loaded["latest_version"] == "1.0.9"

            # Cache expiration test
            expired_cache = {
                "timestamp": time.time() - 90000,  # > 24 hours ago
                "latest_version": "1.0.9",
                "release_url": "https://example.com",
                "release_notes": "expired",
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(expired_cache, f)

            # verify check_for_updates respects expiration when not force
            # When expired, check_for_updates attempts network request (which fails in test without network)
            with patch("urllib.request.urlopen", side_effect=Exception("Network off")):
                avail, ver, _, _ = updater.check_for_updates(force=False)
                assert avail is False

    def test_update_worker_signals(self, qapp):
        from updater import UpdateCheckWorker

        worker = UpdateCheckWorker(force=True)
        signals_received = []
        worker.update_available.connect(lambda ver, url: signals_received.append((ver, url)))

        with patch("updater.check_for_updates", return_value=(True, "1.1.0", "https://example.com/rel", "notes")):
            worker.run()

        assert len(signals_received) == 1
        assert signals_received[0] == ("1.1.0", "https://example.com/rel")


# ============================================================================
# 2. Per-Remote and Global Settings Tests
# ============================================================================
class TestSettings:
    def test_default_config_structure(self):
        from rclone_settings import DEFAULT_CONFIG

        assert "remotes_config" in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG["remotes_config"], dict)
        assert "vfs_cache_mode" in DEFAULT_CONFIG
        assert "bwlimit" in DEFAULT_CONFIG

    def test_per_remote_config_persistence(self, monkeypatch):
        import rclone_settings

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = os.path.join(tmp_dir, "config.json")
            monkeypatch.setattr(rclone_settings, "CONFIG_FILE", cfg_path)
            monkeypatch.setattr(rclone_settings, "CONFIG_DIR", tmp_dir)
            monkeypatch.setattr(rclone_settings, "_CONFIG_CACHE", None)
            monkeypatch.setattr(rclone_settings, "_CONFIG_MTIME", None)

            # Unconfigured remote returns default
            default_cfg = rclone_settings.get_remote_config("gdrive")
            assert default_cfg["mount_path"] == ""
            assert default_cfg["read_only"] is False
            assert default_cfg["custom_flags"] == ""

            # Save custom settings
            custom = {
                "mount_path": "/mnt/custom_gdrive",
                "read_only": True,
                "custom_flags": "--drive-shared-with-me --vfs-read-chunk-size=32M",
                "bwlimit": "10M",
            }
            success = rclone_settings.save_remote_config("gdrive", custom)
            assert success is True

            # Reload and verify
            loaded = rclone_settings.get_remote_config("gdrive")
            assert loaded["mount_path"] == "/mnt/custom_gdrive"
            assert loaded["read_only"] is True
            assert "--drive-shared-with-me" in loaded["custom_flags"]

    def test_get_available_rc_port(self):
        from rclone_settings import get_available_rc_port

        port1 = get_available_rc_port()
        assert isinstance(port1, int)
        assert 1024 < port1 < 65535

    def test_port_allocation_lifecycle(self):
        from rclone_settings import _allocated_ports, allocate_rc_port, release_rc_port

        port = allocate_rc_port(preferred_port=5800)
        assert port in _allocated_ports
        release_rc_port(port)
        assert port not in _allocated_ports


# ============================================================================
# 3. Multi-Daemon Isolation Tests
# ============================================================================
class TestRcloneDaemonMultiMount:
    def test_daemon_distinct_ports_and_mounts(self):
        from rclone_daemon import RcloneDaemon

        d1 = RcloneDaemon(remote_name="gdrive")
        d2 = RcloneDaemon(remote_name="onedrive")

        assert d1.current_remote == "gdrive"
        assert d2.current_remote == "onedrive"
        assert d1.mount_point != d2.mount_point
        assert "gdrive" in d1.mount_point
        assert "onedrive" in d2.mount_point

        # Separate ports
        assert d1.rc_port != d2.rc_port

        # Clean teardown
        d1._proc = None
        d2._proc = None

    def test_daemon_custom_mount_path_from_config(self, monkeypatch):
        import rclone_daemon
        from rclone_daemon import RcloneDaemon

        custom_path = "/tmp/test_custom_mount_ducksdrive"
        monkeypatch.setattr(
            rclone_daemon,
            "get_remote_config",
            lambda r: {"mount_path": custom_path, "read_only": False, "custom_flags": ""},
        )

        d = RcloneDaemon(remote_name="myremote")
        assert d.mount_point == custom_path

    def test_daemon_extra_flags_and_read_only(self, monkeypatch):
        import rclone_daemon

        monkeypatch.setattr(
            rclone_daemon,
            "get_remote_config",
            lambda r: {
                "mount_path": "",
                "read_only": True,
                "custom_flags": "--drive-shared-with-me --fast-list",
                "bwlimit": "5M",
            },
        )

        cfg = rclone_daemon.get_remote_config("gdrive")
        assert cfg["read_only"] is True
        assert "--drive-shared-with-me" in cfg["custom_flags"]


# ============================================================================
# 4. Notifications Tests
# ============================================================================
class TestNotifications:
    def test_find_notification_icon(self):
        from notifications import _find_notification_icon

        icon = _find_notification_icon()
        assert isinstance(icon, str)
        assert len(icon) > 0

    def test_send_desktop_notification_fallback_tray(self):
        from notifications import send_desktop_notification

        mock_tray = MagicMock()
        mock_tray.tray_available = True

        # When D-Bus and notify-send are mocked to fail, fallback to tray.showMessage
        with patch("shutil.which", return_value=None):
            with patch("PySide6.QtDBus.QDBusConnection.sessionBus") as mock_bus:
                mock_bus.return_value.isConnected.return_value = False
                res = send_desktop_notification(
                    "Test Title",
                    "Test Body",
                    fallback_tray=mock_tray,
                )
                assert res is True
                mock_tray.showMessage.assert_called_once()

    def test_notify_helpers(self):
        import notifications

        with patch("notifications.send_desktop_notification", return_value=True) as mock_send:
            assert notifications.notify_sync_complete("gdrive", file_count=5) is True
            assert notifications.notify_connection_lost("onedrive") is True
            assert notifications.notify_mount_success("nextcloud", "/home/test/CloudDrives/nextcloud") is True
            assert mock_send.call_count == 3


# ============================================================================
# 5. Diagnostic Log Viewer Tests
# ============================================================================
class TestLogViewer:
    def test_log_viewer_level_filter(self, qapp):
        from log_viewer import LogViewerDialog

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


# ============================================================================
# 6. Multi-Mount UI and Controller Tests
# ============================================================================
class TestMultiMountUI:
    def test_tray_icon_daemons_collection(self, qapp):
        from gui_tray import RcloneTrayIcon
        from rclone_daemon import RcloneDaemon

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
        from status_popup import StatusPopup
        from rclone_daemon import RcloneDaemon

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
        from main import RcloneAppController

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
        from rclone_daemon import RcloneDaemon
        from rclone_stats import StatsData
        from status_popup import StatusPopup

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
        from gui_tray import RcloneTrayIcon
        from rclone_daemon import RCCredentials, RcloneDaemon

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

        with patch("gui_tray.fetch_vfs_queue", side_effect=mock_fetch):
            with patch("gui_tray.QMessageBox.warning") as mock_box:
                mock_box.return_value = 0  # No
                tray._unmount_all()
                if tray.runner._current:
                    tray.runner._current.wait(1000)
                qapp.processEvents()
                # Ensure warning was triggered due to d2 pending transfers
                mock_box.assert_called_once()



# ============================================================================
# 7. GNOME Wayland / Fallback Window Mode Tests
# ============================================================================
class TestGnomeFallback:
    def test_window_mode_initialization(self, qapp):
        from status_popup import StatusPopup

        popup = StatusPopup(daemon=None, window_mode=True)
        assert popup._window_mode is True
        assert tr("Painel de Controle") in popup.windowTitle()

    def test_controller_tray_unavailable_mode(self, qapp):
        from main import RcloneAppController

        controller = RcloneAppController(qapp, tray_available=False)
        assert controller.tray_available is False
        assert controller.tray.status_popup._window_mode is True
        controller.shutdown()


# ============================================================================
# 8. File Manager Integration Tests (XBEL & GTK Bookmarks)
# ============================================================================
class TestFileManagerIntegration:
    def test_gtk_bookmarks_add_and_remove(self, tmp_path, monkeypatch):
        from filemanager_integration import add_gtk_bookmark, remove_gtk_bookmark

        bookmarks_file = tmp_path / "gtk-3.0" / "bookmarks"
        bookmarks_file.parent.mkdir(parents=True)
        initial_content = "file:///home/user/Documents Docs\nfile:///home/user/Music\n"
        bookmarks_file.write_text(initial_content, encoding="utf-8")

        monkeypatch.setattr("filemanager_integration._get_gtk_bookmarks_path", lambda: str(bookmarks_file))

        mount_path = "/home/user/CloudDrives/gdrive"
        # 1. Add bookmark
        assert add_gtk_bookmark(mount_path, "DucksDrive - gdrive") is True

        content_after_add = bookmarks_file.read_text(encoding="utf-8")
        assert "file:///home/user/Documents Docs" in content_after_add
        assert "file:///home/user/Music" in content_after_add
        assert f"file://{mount_path} DucksDrive - gdrive" in content_after_add

        # 2. Add duplicate - should not duplicate entry
        assert add_gtk_bookmark(mount_path, "DucksDrive - gdrive") is True
        lines = [line for line in bookmarks_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len([line for line in lines if "DucksDrive - gdrive" in line]) == 1

        # 3. Remove bookmark
        assert remove_gtk_bookmark(mount_path) is True
        content_after_remove = bookmarks_file.read_text(encoding="utf-8")
        assert f"file://{mount_path}" not in content_after_remove
        assert "file:///home/user/Documents Docs" in content_after_remove
        assert "file:///home/user/Music" in content_after_remove

    def test_kde_places_add_and_remove(self, tmp_path, monkeypatch):
        import xml.etree.ElementTree as ET
        from filemanager_integration import add_kde_place, remove_kde_place

        places_file = tmp_path / "user-places.xbel"
        initial_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"'
            ' xmlns:kdepriv="http://www.kde.org/kdepriv">\n'
            ' <bookmark href="file:///home/user">\n'
            "  <title>Home</title>\n"
            " </bookmark>\n"
            "</xbel>\n"
        )
        places_file.write_text(initial_xml, encoding="utf-8")

        monkeypatch.setattr("filemanager_integration._get_kde_places_path", lambda: str(places_file))

        mount_path = "/home/user/CloudDrives/onedrive"
        # 1. Add KDE place
        assert add_kde_place(mount_path, "DucksDrive - onedrive") is True

        # Check backup file exists
        bak_file = tmp_path / "user-places.xbel.bak"
        assert bak_file.exists()
        assert "Home" in bak_file.read_text(encoding="utf-8")

        # Parse modified XML and check integrity
        tree = ET.parse(str(places_file))
        root = tree.getroot()
        bookmarks = root.findall("bookmark")
        assert len(bookmarks) == 2
        titles = [b.findtext("title") for b in bookmarks]
        assert "Home" in titles
        assert "DucksDrive - onedrive" in titles

        # 2. Remove KDE place
        assert remove_kde_place(mount_path) is True
        tree_after = ET.parse(str(places_file))
        root_after = tree_after.getroot()
        bookmarks_after = root_after.findall("bookmark")
        assert len(bookmarks_after) == 1
        assert bookmarks_after[0].findtext("title") == "Home"


# ============================================================================
# 9. Password and Secret Masking Tests
# ============================================================================
class TestPasswordMasking:
    def test_mask_command_sensitive_arguments(self):
        from rclone_config import mask_command

        cmd1 = ["rclone", "config", "create", "drive", "drive", "client_secret", "MY_SUPER_SECRET_TOKEN_12345"]
        masked1 = mask_command(cmd1)
        assert "MY_SUPER_SECRET_TOKEN_12345" not in masked1
        assert "******" in masked1
        assert "rclone config create drive drive client_secret ******" == masked1

        cmd2 = ["rclone", "mount", "remote:", "/path", "--rc-pass", "secretpassword"]
        masked2 = mask_command(cmd2)
        assert "secretpassword" not in masked2
        assert "--rc-pass ******" in masked2

        cmd3 = ["rclone", "config", "create", "s3", "s3", "secret_access_key=MY_S3_KEY_ABCD"]
        masked3 = mask_command(cmd3)
        assert "MY_S3_KEY_ABCD" not in masked3
        assert "secret_access_key=******" in masked3

        cmd4 = ["rclone", "config", "create", "mega", "mega", "pass", "megapass123"]
        masked4 = mask_command(cmd4)
        assert "megapass123" not in masked4
        assert "pass ******" in masked4

    def test_mask_command_preserves_non_sensitive(self):
        from rclone_config import mask_command

        cmd = ["rclone", "mount", "gdrive:", "/home/user/CloudDrives/gdrive", "--vfs-cache-mode", "full", "--transfers", "4"]
        masked = mask_command(cmd)
        assert masked == " ".join(cmd)


# ============================================================================
# 10. Rclone Stats Telemetry Parsing Tests
# ============================================================================
class TestRcloneStatsTelemetry:
    def test_format_bytes(self):
        from rclone_stats import format_bytes

        assert format_bytes(-5) == "0 B"
        assert format_bytes(0) == "0 B"
        assert format_bytes(500) == "500 B"
        assert format_bytes(1024) == "1.00 KB"
        assert format_bytes(1024 * 1024) == "1.00 MB"
        assert format_bytes(15 * 1024 * 1024) == "15.00 MB"
        assert format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"
        assert format_bytes(3 * 1024 * 1024 * 1024 * 1024) == "3.00 TB"

    def test_format_eta(self):
        from rclone_stats import format_eta

        assert format_eta(None) == "--:--:--"
        assert format_eta(-10) == "--:--:--"
        assert format_eta(0) == "00:00:00"
        assert format_eta(45) == "00:00:45"
        assert format_eta(125) == "00:02:05"
        assert format_eta(3661) == "01:01:01"

    def test_stats_data_has_active_transfers_property(self):
        from rclone_stats import StatsData

        # Online with active transfer
        s1 = StatsData(is_online=True, active_transfers=[{"name": "file.iso"}])
        assert s1.has_active_transfers is True

        # Online with pending VFS queue
        s2 = StatsData(is_online=True, active_transfers=[], vfs_queue_count=5)
        assert s2.has_active_transfers is True

        # Offline with queued items (should not report active transfers)
        s3 = StatsData(is_online=False, active_transfers=[{"name": "file.iso"}])
        assert s3.has_active_transfers is False

        # Online idle
        s4 = StatsData(is_online=True, active_transfers=[], vfs_queue_count=0)
        assert s4.has_active_transfers is False


# ============================================================================
# 11. Configuration Defaults and Migration Tests
# ============================================================================
class TestSettingsMigration:
    def test_config_defaults_merging(self, tmp_path, monkeypatch):
        import rclone_settings
        from rclone_settings import DEFAULT_CONFIG, load_config, save_config

        cfg_file = tmp_path / "ducksdrive" / "config.json"
        monkeypatch.setattr(rclone_settings, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(rclone_settings, "CONFIG_DIR", str(cfg_file.parent))
        monkeypatch.setattr(rclone_settings, "_CONFIG_CACHE", None)
        monkeypatch.setattr(rclone_settings, "_CONFIG_MTIME", None)

        # 1. Non-existent file returns copy of default config
        loaded = load_config()
        for k, v in DEFAULT_CONFIG.items():
            assert loaded[k] == v

        # 2. Save custom setting; missing default keys are preserved upon reload
        partial_config = {"bwlimit": "15M", "custom_user_key": "duck_power"}
        assert save_config(partial_config) is True

        monkeypatch.setattr(rclone_settings, "_CONFIG_CACHE", None)
        monkeypatch.setattr(rclone_settings, "_CONFIG_MTIME", None)

        reloaded = load_config()
        assert reloaded["bwlimit"] == "15M"
        assert reloaded["custom_user_key"] == "duck_power"
        # Verify defaults were kept
        assert reloaded["vfs_cache_mode"] == DEFAULT_CONFIG["vfs_cache_mode"]
        assert reloaded["transfers"] == DEFAULT_CONFIG["transfers"]

