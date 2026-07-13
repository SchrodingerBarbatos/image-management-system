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


def test_load_config_with_token_does_not_write_auth_state(conf):
    """Reading a config that contains a token must not create/enable auth_state."""
    # Write config file directly without going through save_config
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_token": "leaked-read-only"}, f)
    conf._cached_config = None
    conf._last_good_token = None
    # Ensure no prior auth state
    if os.path.exists(conf.AUTH_STATE_PATH):
        os.remove(conf.AUTH_STATE_PATH)
    cfg, err = conf.load_config()
    assert err is None
    assert cfg.get("api_token") == "leaked-read-only"
    # auth_state must still be absent — only save_config writes it
    assert not os.path.exists(conf.AUTH_STATE_PATH)
    # get_api_token can use in-memory last_good for this process
    token, fail_closed = conf.get_api_token()
    assert token == "leaked-read-only"
    assert fail_closed is False
    # But after memory wipe without auth_state, must be open (never auto-enabled)
    conf._cached_config = None
    conf._last_good_token = None
    token2, fail2 = conf.get_api_token()
    assert token2 == "leaked-read-only" or (token2 == "" and fail2 is False)
    # Re-read file into memory again then wipe — still no auth_state file
    conf.load_config()
    conf._cached_config = None
    conf._last_good_token = None
    assert not os.path.exists(conf.AUTH_STATE_PATH)
    token3, fail3 = conf.get_api_token()
    # Without auth_state and without memory, open mode (token may come from re-read of config)
    # Config file still exists with token, so load succeeds and returns token
    assert fail3 is False
    assert token3 == "leaked-read-only"


def test_only_save_config_enables_persisted_flag(conf):
    conf.save_config({"api_token": "via-save"})
    assert os.path.exists(conf.AUTH_STATE_PATH)
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True

