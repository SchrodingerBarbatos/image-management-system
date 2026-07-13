"""Tests for API token persistence / fail-closed auth state."""

import json
import os

import pytest


@pytest.fixture()
def conf(tmp_path, monkeypatch):
    """Isolate config module state + paths under tmp_path."""
    import config as conf_mod

    monkeypatch.setattr(conf_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(conf_mod, "CONFIG_PATH", str(tmp_path / "app_config.json"))
    monkeypatch.setattr(conf_mod, "AUTH_STATE_PATH", str(tmp_path / "auth_state.json"))
    # Reset in-memory state
    conf_mod._cached_config = None
    conf_mod._last_good_token = None
    return conf_mod


def test_first_run_open_mode(conf):
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is False


def test_enable_token_and_load(conf):
    conf.save_config({"debug_mode": False, "api_token": "secret-1"})
    token, fail_closed = conf.get_api_token()
    assert token == "secret-1"
    assert fail_closed is False
    # Persisted flag
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True


def test_config_deleted_after_enable_still_uses_memory_token(conf):
    conf.save_config({"api_token": "secret-2"})
    os.remove(conf.CONFIG_PATH)
    token, fail_closed = conf.get_api_token()
    assert token == "secret-2"
    assert fail_closed is False


def test_config_deleted_and_memory_cleared_fail_closed(conf):
    """Simulate restart: config gone, memory empty, auth_state says enabled."""
    conf.save_config({"api_token": "secret-3"})
    os.remove(conf.CONFIG_PATH)
    # Simulate process restart — wipe memory only
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_config_corrupt_after_enable_fail_closed(conf):
    conf.save_config({"api_token": "secret-4"})
    # Corrupt config file
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("{not-json")
    # Wipe memory to simulate restart (auth_state still true)
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_explicit_disable_token_open_mode(conf):
    conf.save_config({"api_token": "secret-5"})
    conf.save_config({"api_token": ""})  # intentional disable
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is False
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is False


def test_auth_state_corrupt_fail_closed(conf):
    conf.save_config({"api_token": "secret-6"})
    conf._cached_config = None
    conf._last_good_token = None
    os.remove(conf.CONFIG_PATH)
    with open(conf.AUTH_STATE_PATH, "w", encoding="utf-8") as f:
        f.write("!!!")
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_corrupt_config_never_had_token_fail_closed_if_file_exists(conf):
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("{bad")
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True
