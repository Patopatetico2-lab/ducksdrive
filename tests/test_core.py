"""
tests/test_core.py - Core settings, daemon, config, telemetry, and resource tests for DucksDrive.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch


# ============================================================================
# 1. Resources and Package Entrypoint Tests
# ============================================================================
class TestResourcesAndEntrypoint:
    def test_get_icon_path_finds_bundled_svg(self):
        from ducksdrive.resources import get_icon_path

        icon_path = get_icon_path()
        assert icon_path is not None
        assert os.path.isfile(icon_path)
        assert icon_path.replace("\\", "/").endswith("ducksdrive/assets/icon.svg")

    def test_autostart_source_checkout_fallback_points_to_package_entrypoint(self, monkeypatch):
        from ducksdrive.core import rclone_settings

        monkeypatch.delenv("FLATPAK_ID", raising=False)
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.setattr(rclone_settings, "_is_executable_file", lambda _p: False)
        monkeypatch.setattr(rclone_settings.shutil, "which", lambda _name: None)

        exec_cmd, _try_exec, icon_path = rclone_settings._resolve_autostart_command()
        assert "ducksdrive/__main__.py" in exec_cmd.replace("\\", "/")
        assert "--autostart" in exec_cmd
        assert icon_path.endswith("icon.svg")


# ============================================================================
# 2. Per-Remote and Global Settings Tests
# ============================================================================
class TestSettings:
    def test_default_config_structure(self):
        from ducksdrive.core.rclone_settings import DEFAULT_CONFIG

        assert "remotes_config" in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG["remotes_config"], dict)
        assert "vfs_cache_mode" in DEFAULT_CONFIG
        assert "bwlimit" in DEFAULT_CONFIG

    def test_per_remote_config_persistence(self, monkeypatch):
        from ducksdrive.core import rclone_settings

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
        from ducksdrive.core.rclone_settings import get_available_rc_port

        port1 = get_available_rc_port()
        assert isinstance(port1, int)
        assert 1024 < port1 < 65535

    def test_port_allocation_lifecycle(self):
        from ducksdrive.core.rclone_settings import _allocated_ports, allocate_rc_port, release_rc_port

        port = allocate_rc_port(preferred_port=5800)
        assert port in _allocated_ports
        release_rc_port(port)
        assert port not in _allocated_ports


# ============================================================================
# 3. Configuration Defaults and Migration Tests
# ============================================================================
class TestSettingsMigration:
    def test_config_defaults_merging(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_settings
        from ducksdrive.core.rclone_settings import DEFAULT_CONFIG, load_config, save_config

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


# ============================================================================
# 4. Multi-Daemon Isolation Tests
# ============================================================================
class TestRcloneDaemonMultiMount:
    def test_daemon_distinct_ports_and_mounts(self):
        from ducksdrive.core.rclone_daemon import RcloneDaemon

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
        from ducksdrive.core import rclone_daemon
        from ducksdrive.core.rclone_daemon import RcloneDaemon

        custom_path = "/tmp/test_custom_mount_ducksdrive"
        monkeypatch.setattr(
            rclone_daemon,
            "get_remote_config",
            lambda r: {"mount_path": custom_path, "read_only": False, "custom_flags": ""},
        )

        d = RcloneDaemon(remote_name="myremote")
        assert d.mount_point == custom_path

    def test_daemon_extra_flags_and_read_only(self, monkeypatch):
        from ducksdrive.core import rclone_daemon

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
# 5. Password and Secret Masking Tests
# ============================================================================
class TestPasswordMasking:
    def test_mask_command_sensitive_arguments(self):
        from ducksdrive.core.rclone_config import mask_command

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
        from ducksdrive.core.rclone_config import mask_command

        cmd = ["rclone", "mount", "gdrive:", "/home/user/CloudDrives/gdrive", "--vfs-cache-mode", "full", "--transfers", "4"]
        masked = mask_command(cmd)
        assert masked == " ".join(cmd)


# ============================================================================
# 6. Rclone Stats Telemetry Parsing Tests
# ============================================================================
class TestRcloneStatsTelemetry:
    def test_format_bytes(self):
        from ducksdrive.core.rclone_stats import format_bytes

        assert format_bytes(-5) == "0 B"
        assert format_bytes(0) == "0 B"
        assert format_bytes(500) == "500 B"
        assert format_bytes(1024) == "1.00 KB"
        assert format_bytes(1024 * 1024) == "1.00 MB"
        assert format_bytes(15 * 1024 * 1024) == "15.00 MB"
        assert format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"
        assert format_bytes(3 * 1024 * 1024 * 1024 * 1024) == "3.00 TB"

    def test_format_eta(self):
        from ducksdrive.core.rclone_stats import format_eta

        assert format_eta(None) == "--:--:--"
        assert format_eta(-10) == "--:--:--"
        assert format_eta(0) == "00:00:00"
        assert format_eta(45) == "00:00:45"
        assert format_eta(125) == "00:02:05"
        assert format_eta(3661) == "01:01:01"

    def test_stats_data_has_active_transfers_property(self):
        from ducksdrive.core.rclone_stats import StatsData

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
# 7. RC Unix Domain Socket and Session Auth Tests
# ============================================================================
class TestRCUnixSocketAndAuth:
    def test_rc_credentials_unix_socket_generation(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_daemon

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        creds = rclone_daemon.generate_rc_credentials(remote_name="gdrive:", use_unix_socket=True)
        assert creds.is_unix_socket is True
        assert creds.socket_path is not None
        assert creds.socket_path.endswith("rc-gdrive.sock")
        assert creds.user.startswith("rc_user_")
        assert len(creds.password) >= 20
        assert creds.url == "http://localhost"
        assert creds.auth_header.startswith("Basic ")

    def test_rc_credentials_tcp_fallback(self):
        from ducksdrive.core import rclone_daemon

        creds = rclone_daemon.generate_rc_credentials(host="127.0.0.1", port=5580, use_unix_socket=False)
        assert creds.is_unix_socket is False
        assert creds.socket_path is None
        assert creds.url == "http://127.0.0.1:5580"

    def test_rc_request_routing_mock(self):
        from ducksdrive.core.rclone_daemon import RCCredentials
        from ducksdrive.core.rclone_stats import rc_request

        tcp_creds = RCCredentials(user="user", password="pwd", host="127.0.0.1", port=5572)
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"result": "ok"}'
            mock_url.return_value.__enter__.return_value = mock_resp
            status, data = rc_request(tcp_creds, "/core/version")
            assert status == 200
            assert data == {"result": "ok"}

    def test_teardown_cleans_unix_socket(self, tmp_path):
        from ducksdrive.core.rclone_daemon import RCCredentials, _teardown_mount

        sock_file = tmp_path / "rc-test.sock"
        sock_file.touch()
        assert sock_file.exists()

        creds = RCCredentials(user="u", password="p", socket_path=str(sock_file))
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0

        _teardown_mount(mock_proc, str(tmp_path / "mount"), creds=creds)
        assert not sock_file.exists()


# ============================================================================
# 8. Dynamic Bandwidth and VFS RC Controls Tests
# ============================================================================
class TestDynamicBandwidthAndVFSControls:
    def test_set_rc_bwlimit(self):
        from ducksdrive.core import rclone_stats
        from ducksdrive.core.rclone_daemon import RCCredentials

        creds = RCCredentials(user="u", password="p", host="127.0.0.1", port=5572)
        with patch.object(rclone_stats, "rc_request", return_value=(200, {})) as mock_rc:
            assert rclone_stats.set_rc_bwlimit(creds, "10M") is True
            mock_rc.assert_called_with(creds, "/core/bwlimit", {"rate": "10M"}, timeout=5.0)

    def test_rc_vfs_forget(self):
        from ducksdrive.core import rclone_stats
        from ducksdrive.core.rclone_daemon import RCCredentials

        creds = RCCredentials(user="u", password="p", host="127.0.0.1", port=5572)
        with patch.object(rclone_stats, "rc_request", return_value=(200, {})) as mock_rc:
            assert rclone_stats.rc_vfs_forget(creds) is True
            mock_rc.assert_called_with(creds, "/vfs/forget", timeout=5.0)


# ============================================================================
# 9. Core Regressions & Performance Tests
# ============================================================================
class TestCoreRegressions:
    def test_clean_env_injects_rclone_password_command(self, monkeypatch):
        from ducksdrive.core import rclone_config, rclone_settings
        from ducksdrive.system import keyring_manager

        monkeypatch.setattr(rclone_settings, "load_config", lambda: {"use_system_keyring": True})
        monkeypatch.setattr(keyring_manager, "is_keyring_available", lambda: True)
        monkeypatch.setattr(keyring_manager, "get_password_command", lambda: "secret-tool lookup service ducksdrive user rclone")

        env = rclone_config.get_clean_env()
        assert env.get("RCLONE_PASSWORD_COMMAND") == "secret-tool lookup service ducksdrive user rclone"

        monkeypatch.setattr(rclone_settings, "load_config", lambda: {"use_system_keyring": False})
        env_off = rclone_config.get_clean_env()
        assert "RCLONE_PASSWORD_COMMAND" not in env_off

    def test_fast_remote_names_from_conf_and_encrypted_fallback(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_config

        conf = tmp_path / "rclone.conf"
        conf.write_text("[onedrive]\ntype = onedrive\n\n[gdrive]\ntype = drive\n", encoding="utf-8")
        monkeypatch.setenv("RCLONE_CONFIG", str(conf))
        monkeypatch.setattr(rclone_config, "_REMOTE_NAMES_CACHE", None)

        with patch("ducksdrive.core.rclone_config.list_remotes") as mock_list:
            names = rclone_config.list_remote_names()
            assert names == ["gdrive", "onedrive"]
            mock_list.assert_not_called()

        # Encrypted rclone.conf must fall back to list_remotes()
        enc_conf = tmp_path / "rclone_enc.conf"
        enc_conf.write_text("# Encrypted rclone configuration File\nRCLONE_ENCRYPT_V0:\nabc123\n", encoding="utf-8")
        monkeypatch.setenv("RCLONE_CONFIG", str(enc_conf))
        monkeypatch.setattr(rclone_config, "_REMOTE_NAMES_CACHE", None)

        with patch("ducksdrive.core.rclone_config.list_remotes", return_value={"vault": {"type": "drive"}}) as mock_list:
            names = rclone_config.list_remote_names()
            assert names == ["vault"]
            mock_list.assert_called_once()

    def test_daemon_is_running_false_during_starting_and_unmounting(self, qapp):
        from ducksdrive.core.rclone_daemon import RcloneDaemon

        d = RcloneDaemon(remote_name="gdrive")
        d.process = MagicMock()
        d.process.poll.return_value = None

        d.state = "starting"
        assert d.is_running() is False
        assert d.is_process_alive() is True

        d.state = "mounted"
        assert d.is_running() is True

        d.state = "unmounting"
        assert d.is_running() is False
        assert d.is_process_alive() is True

    def test_autostart_desktop_entry_format_and_flags(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_settings

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        monkeypatch.delenv("APPIMAGE", raising=False)
        fake_bin = tmp_path / "bin" / "ducksdrive"
        fake_bin.parent.mkdir(parents=True)
        fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_bin.chmod(0o755)
        monkeypatch.setenv("XDG_BIN_HOME", str(fake_bin.parent))

        rclone_settings._sync_autostart(True)
        desktop = tmp_path / "cfg" / "autostart" / "ducksdrive.desktop"
        assert desktop.exists()
        text = desktop.read_text(encoding="utf-8")
        assert f"TryExec={fake_bin}" in text
        assert f"Exec={fake_bin} --autostart" in text
        assert 'Exec="' not in text
        assert "StartupNotify=false" in text
        assert "Hidden=false" in text
        assert "X-GNOME-Autostart-enabled=true" in text

        rclone_settings._sync_autostart(False)
        assert not desktop.exists()

    def test_load_config_returns_deep_copy_without_mutating_defaults(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_settings

        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr(rclone_settings, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(rclone_settings, "CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(rclone_settings, "_CONFIG_CACHE", None)
        monkeypatch.setattr(rclone_settings, "_CONFIG_MTIME", None)

        c1 = rclone_settings.load_config()
        c1["remotes_config"]["ghost"] = {"read_only": True}
        assert "ghost" not in rclone_settings.DEFAULT_CONFIG["remotes_config"]
        c2 = rclone_settings.load_config()
        assert "ghost" not in c2["remotes_config"]

    def test_supports_vfs_read_chunk_streams_version_detection(self, monkeypatch):
        from ducksdrive.core import rclone_config

        monkeypatch.setattr(rclone_config, "_VFS_CHUNK_STREAMS_SUPPORT_CACHE", None)
        fake_res = MagicMock()
        fake_res.returncode = 0
        fake_res.stdout = "rclone v1.76.0\n- os/version: cachyos\n"
        with patch("ducksdrive.core.rclone_config.subprocess.run", return_value=fake_res):
            assert rclone_config.supports_vfs_read_chunk_streams("/usr/bin/rclone") is True

        monkeypatch.setattr(rclone_config, "_VFS_CHUNK_STREAMS_SUPPORT_CACHE", None)
        fake_old = MagicMock()
        fake_old.returncode = 0
        fake_old.stdout = "rclone v1.62.2\n"
        with patch("ducksdrive.core.rclone_config.subprocess.run", return_value=fake_old):
            assert rclone_config.supports_vfs_read_chunk_streams("/usr/bin/rclone") is False

    def test_drive_client_credentials_read_and_update(self, tmp_path, monkeypatch):
        from ducksdrive.core import rclone_config

        conf = tmp_path / "rclone.conf"
        conf.write_text(
            "[drive]\ntype = drive\nclient_id = my_id_123.apps.googleusercontent.com\nclient_secret = GOCSPX-secret999\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("RCLONE_CONFIG", str(conf))
        cid, csec = rclone_config.get_drive_client_credentials("drive:")
        assert cid == "my_id_123.apps.googleusercontent.com"
        assert csec == "GOCSPX-secret999"
        assert rclone_config.has_custom_drive_client_id("drive") is True

        fake_upd = MagicMock()
        fake_upd.returncode = 0
        fake_upd.stdout = ""
        fake_upd.stderr = ""
        with patch("ducksdrive.core.rclone_config.get_rclone_path", return_value="/usr/bin/rclone"):
            with patch("ducksdrive.core.rclone_config.subprocess.run", return_value=fake_upd) as mock_run:
                ok, _ = rclone_config.update_drive_client_credentials("drive", "new_id", "new_secret")
                assert ok is True
                called_cmd = mock_run.call_args.args[0]
                assert "client_id" in called_cmd
                assert "new_id" in called_cmd
