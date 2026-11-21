"""
tests/test_system.py - Updater, notifications, file manager integration, inhibitor, keyring, and i18n tests.
"""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch


# ============================================================================
# 1. Updater Tests
# ============================================================================
class TestUpdater:
    def test_parse_version_standard(self):
        from ducksdrive.system.updater import parse_version

        assert parse_version("1.0.8") == (1, 0, 8)
        assert parse_version("v1.0.9") == (1, 0, 9)
        assert parse_version("v2.15.30") == (2, 15, 30)

    def test_parse_version_pre_release(self):
        from ducksdrive.system.updater import parse_version

        assert parse_version("v1.0.9-beta.1") == (1, 0, 9)
        assert parse_version("1.10.0-rc2") == (1, 10, 0)

    def test_parse_version_invalid(self):
        from ducksdrive.system.updater import parse_version

        assert parse_version("invalid") == (0, 0, 0)
        assert parse_version("") == (0, 0, 0)

    def test_version_comparisons(self):
        from ducksdrive.system.updater import parse_version

        assert parse_version("1.0.9") > parse_version("1.0.8")
        assert parse_version("1.0.10") > parse_version("1.0.9")
        assert parse_version("2.0.0") > parse_version("1.99.99")
        assert parse_version("1.0.9") == parse_version("v1.0.9")

    def test_update_cache_persistence(self, monkeypatch):
        from ducksdrive.system import updater

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
        from ducksdrive.system.updater import UpdateCheckWorker

        worker = UpdateCheckWorker(force=True)
        signals_received = []
        worker.update_available.connect(lambda ver, url: signals_received.append((ver, url)))

        with patch(
            "ducksdrive.system.updater.check_for_updates",
            return_value=(True, "1.1.0", "https://example.com/rel", "notes"),
        ):
            worker.run()

        assert len(signals_received) == 1
        assert signals_received[0] == ("1.1.0", "https://example.com/rel")

    def test_updater_force_check_emits_failed_when_offline(self, qapp):
        from ducksdrive.system.updater import UpdateCheckWorker

        worker = UpdateCheckWorker(force=True)
        failed_msgs = []
        finished_calls = []
        worker.check_failed.connect(lambda msg: failed_msgs.append(msg))
        worker.check_finished.connect(lambda *args: finished_calls.append(args))

        with patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            worker.run()

        assert len(failed_msgs) == 1
        assert len(finished_calls) == 0


# ============================================================================
# 2. Notifications Tests
# ============================================================================
class TestNotifications:
    def test_find_notification_icon(self):
        from ducksdrive.system.notifications import _find_notification_icon

        icon = _find_notification_icon()
        assert isinstance(icon, str)
        assert len(icon) > 0

    def test_send_desktop_notification_fallback_tray(self):
        from ducksdrive.system.notifications import send_desktop_notification

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
        from ducksdrive.system import notifications

        with patch("ducksdrive.system.notifications.send_desktop_notification", return_value=True) as mock_send:
            assert notifications.notify_sync_complete("gdrive", file_count=5) is True
            assert notifications.notify_connection_lost("onedrive") is True
            assert notifications.notify_mount_success("nextcloud", "/home/test/CloudDrives/nextcloud") is True
            assert mock_send.call_count == 3


# ============================================================================
# 3. File Manager Integration Tests (XBEL & GTK Bookmarks)
# ============================================================================
class TestFileManagerIntegration:
    def test_gtk_bookmarks_add_and_remove(self, tmp_path, monkeypatch):
        from ducksdrive.system.filemanager_integration import add_gtk_bookmark, remove_gtk_bookmark

        bookmarks_file = tmp_path / "gtk-3.0" / "bookmarks"
        bookmarks_file.parent.mkdir(parents=True)
        initial_content = "file:///home/user/Documents Docs\nfile:///home/user/Music\n"
        bookmarks_file.write_text(initial_content, encoding="utf-8")

        monkeypatch.setattr(
            "ducksdrive.system.filemanager_integration._get_gtk_bookmarks_path",
            lambda: str(bookmarks_file),
        )

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
        from ducksdrive.system.filemanager_integration import add_kde_place, remove_kde_place

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

        monkeypatch.setattr(
            "ducksdrive.system.filemanager_integration._get_kde_places_path",
            lambda: str(places_file),
        )

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

    def test_kde_places_preserves_doctype_and_custom_namespaces(self, tmp_path, monkeypatch):
        from ducksdrive.system.filemanager_integration import add_kde_place, remove_kde_place

        places_file = tmp_path / "user-places.xbel"
        initial_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE xbel>\n'
            '<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"'
            ' xmlns:kdepriv="http://www.kde.org/kdepriv"'
            ' xmlns:custom="http://example.org/custom">\n'
            ' <bookmark href="file:///home/user">\n'
            '  <title>Home</title>\n'
            '  <info><metadata owner="http://example.org"><custom:flag>1</custom:flag></metadata></info>\n'
            ' </bookmark>\n'
            '</xbel>\n'
        )
        places_file.write_text(initial_xml, encoding="utf-8")
        monkeypatch.setattr(
            "ducksdrive.system.filemanager_integration._get_kde_places_path",
            lambda: str(places_file),
        )

        assert add_kde_place("/home/user/CloudDrives/gdrive", "DucksDrive - gdrive") is True
        content = places_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE xbel>" in content
        assert "ns0:" not in content
        assert "custom:flag" in content

        assert remove_kde_place("/home/user/CloudDrives/gdrive") is True
        content_after = places_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE xbel>" in content_after
        assert "DucksDrive - gdrive" not in content_after


# ============================================================================
# 4. VFS Inhibitor Tests (Shutdown / Sleep Delay Protection)
# ============================================================================
class TestVFSInhibitor:
    def test_inhibitor_acquire_and_release(self, monkeypatch):
        from ducksdrive.system.vfs_inhibitor import VFSInhibitorManager

        inhibitor = VFSInhibitorManager()
        # Mock systemd-inhibit binary
        monkeypatch.setattr(inhibitor, "_inhibitor_bin", None)

        signal_records = []
        inhibitor.lock_state_changed.connect(lambda locked, count: signal_records.append((locked, count)))

        # 1. Acquire lock on pending queue
        assert inhibitor.acquire_lock(pending_count=4) is True
        assert inhibitor.is_locked is True
        assert inhibitor.pending_count == 4
        assert (True, 4) in signal_records

        # 2. Update queue status while active
        inhibitor.update_queue_status(total_pending=2)
        assert inhibitor.is_locked is True
        assert inhibitor.pending_count == 2

        # 3. Release lock when queue drops to 0
        with patch("ducksdrive.system.vfs_inhibitor.notify_info") as mock_notify:
            inhibitor.update_queue_status(total_pending=0)
            assert inhibitor.is_locked is False
            assert inhibitor.pending_count == 0
            assert (False, 0) in signal_records
            mock_notify.assert_called_once()

        # 4. Clean shutdown
        inhibitor.shutdown()
        assert inhibitor.is_locked is False

    def test_inhibitor_does_not_spam_when_binary_fails(self, monkeypatch):
        from ducksdrive.system.vfs_inhibitor import VFSInhibitorManager

        inhibitor = VFSInhibitorManager()
        monkeypatch.setattr(inhibitor, "_inhibitor_bin", "/usr/bin/systemd-inhibit")

        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_proc.poll.return_value = 1
        fake_proc.returncode = 1

        with patch("ducksdrive.system.vfs_inhibitor.subprocess.Popen", return_value=fake_proc) as mock_popen:
            with patch("ducksdrive.system.vfs_inhibitor.notify_warning") as mock_warn:
                assert inhibitor.acquire_lock(pending_count=2) is True
                # Next poll sees process exited with rc=1 -> disables retries instead of respawning
                assert inhibitor.acquire_lock(pending_count=2) is False
                assert mock_popen.call_count == 1
                assert mock_warn.call_count == 1


# ============================================================================
# 5. Keyring Integration Tests (Secret Service API / secret-tool)
# ============================================================================
class TestKeyringIntegration:
    def test_password_command_string(self):
        from ducksdrive.system.keyring_manager import get_password_command

        cmd = get_password_command(service="ducksdrive", user="rclone")
        assert cmd == "secret-tool lookup service ducksdrive user rclone"

    def test_mask_command_protects_password_command(self):
        from ducksdrive.core.rclone_config import mask_command

        cmd = ["rclone", "mount", "gdrive:", "/home/user/CloudDrives/gdrive", "--password-command", "secret-tool lookup service ducksdrive user rclone"]
        masked = mask_command(cmd)
        assert "secret-tool" not in masked
        assert "--password-command ******" in masked

    def test_keyring_store_lookup_clear_mocked(self, monkeypatch):
        from ducksdrive.system import keyring_manager

        monkeypatch.setattr(keyring_manager, "is_keyring_available", lambda: True)
        monkeypatch.setattr(keyring_manager.shutil, "which", lambda cmd: "/usr/bin/secret-tool")

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "my_master_password\n"
            mock_run.return_value = mock_proc

            # Store
            assert keyring_manager.store_rclone_password("my_master_password") is True
            # Lookup
            assert keyring_manager.get_rclone_password() == "my_master_password"
            # Clear
            assert keyring_manager.clear_rclone_password() is True


# ============================================================================
# 6. Internationalization Tests
# ============================================================================
class TestI18n:
    def test_en_us_translations_complete(self):
        from ducksdrive.i18n import set_language, tr

        try:
            set_language("en_US")
            assert tr("Banda:") == "Bandwidth:"
            assert tr("Atualizar Pastas") == "Refresh Folders"
            assert tr("Testar Conexão") == "Test Connection"
            assert tr("Não mostrar esta mensagem novamente") == "Do not show this message again"
            assert tr("100 MB/s (Ultra Rápido)") == "100 MB/s (Ultra Fast)"
            assert tr("Fechar") == "Close"
            assert tr("Tamanho Máximo do Cache:") == "Max Cache Size:"
        finally:
            set_language("pt_BR")
