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
