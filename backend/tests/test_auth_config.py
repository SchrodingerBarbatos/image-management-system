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
    conf_mod._auth_persist_failed = False
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


def test_auth_state_empty_object_fail_closed(conf):
    conf.save_config({"api_token": "secret-7"})
    conf._cached_config = None
    conf._last_good_token = None
    os.remove(conf.CONFIG_PATH)
    with open(conf.AUTH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({}, f)
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_auth_state_zero_token_enabled_fail_closed(conf):
    conf.save_config({"api_token": "secret-8"})
    conf._cached_config = None
    conf._last_good_token = None
    os.remove(conf.CONFIG_PATH)
    with open(conf.AUTH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"token_enabled": 0}, f)
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_auth_state_empty_string_token_enabled_fail_closed(conf):
    conf.save_config({"api_token": "secret-9"})
    conf._cached_config = None
    conf._last_good_token = None
    os.remove(conf.CONFIG_PATH)
    with open(conf.AUTH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"token_enabled": ""}, f)
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_migrate_auth_state_from_legacy_config(conf):
    """Hand-edited app_config with token + no auth_state → migration enables flag."""
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_token": "legacy-secret"}, f)
    conf._cached_config = None
    conf._last_good_token = None
    if os.path.exists(conf.AUTH_STATE_PATH):
        os.remove(conf.AUTH_STATE_PATH)
    assert conf.migrate_auth_state_from_config() == "updated"
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True
    # Simulate restart: config deleted, memory empty — must fail-closed
    os.remove(conf.CONFIG_PATH)
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_reconcile_reenables_when_marker_false_and_token_set(conf):
    """marker=false then hand-edit non-empty token → sync true; config loss fail-closed."""
    conf.save_config({"api_token": ""})  # token_enabled=false
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_token": "re-enabled-secret"}, f)
    assert conf.migrate_auth_state_from_config() == "updated"
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True
    # Config deleted + memory wipe → must fail-closed
    os.remove(conf.CONFIG_PATH)
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_reconcile_disables_when_marker_true_and_token_empty(conf):
    """marker=true then hand-edit empty token → sync false; restart open mode."""
    conf.save_config({"api_token": "was-on"})
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_token": ""}, f)
    assert conf.migrate_auth_state_from_config() == "updated"
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is False
    conf._cached_config = None
    conf._last_good_token = None
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is False


def test_reconcile_noop_when_api_token_key_absent(conf):
    """Config without api_token key must not flip an existing marker."""
    conf.save_config({"api_token": "keep-enabled"})
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"debug_mode": True}, f)  # no api_token field
    assert conf.migrate_auth_state_from_config() == "no_change"
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True


def test_migrate_noop_when_no_token_in_config(conf):
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"debug_mode": True}, f)
    assert conf.migrate_auth_state_from_config() == "no_change"
    assert not os.path.exists(conf.AUTH_STATE_PATH)


def test_reconcile_noop_when_already_in_sync(conf):
    conf.save_config({"api_token": "same"})
    assert conf.migrate_auth_state_from_config() == "no_change"
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True


def test_enable_write_failure_returns_failed_and_fail_closed(conf, monkeypatch):
    """Inject marker write failure while enabling → 'failed' + fail-closed latch."""
    conf.save_config({"api_token": ""})  # marker false
    with open(conf.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"api_token": "cannot-persist"}, f)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(conf, "_atomic_write_json", boom)
    assert conf.migrate_auth_state_from_config() == "failed"
    assert conf._auth_persist_failed is True
    # Marker still false on disk (write failed)
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is False
    # Config subsequently lost (the fail-open scenario we must prevent)
    os.remove(conf.CONFIG_PATH)
    conf._cached_config = None
    conf._last_good_token = None
    # Even with marker false, process latch forces fail-closed (not open)
    token, fail_closed = conf.get_api_token()
    assert token == ""
    assert fail_closed is True


def test_save_config_enable_write_failure_raises(conf, monkeypatch):
    def boom(path, payload):
        # Allow config write, fail only auth_state
        if path == conf.AUTH_STATE_PATH:
            raise OSError("permission denied")
        # real write for config
        import json as _json
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(payload, f)

    monkeypatch.setattr(conf, "_atomic_write_json", boom)
    with pytest.raises(conf.AuthStateError):
        conf.save_config({"api_token": "x"})
    assert conf._auth_persist_failed is True


def test_successful_disable_clears_enable_write_failure_latch(conf, monkeypatch):
    """A recovered marker write must allow intentional open mode immediately."""
    conf.save_config({"api_token": ""})
    real_atomic_write_json = conf._atomic_write_json

    def fail_enabled_state(path, payload):
        if path == conf.AUTH_STATE_PATH and payload.get("token_enabled") is True:
            raise OSError("disk temporarily unavailable")
        return real_atomic_write_json(path, payload)

    monkeypatch.setattr(conf, "_atomic_write_json", fail_enabled_state)
    with pytest.raises(conf.AuthStateError):
        conf.save_config({"api_token": "cannot-persist"})
    assert conf._auth_persist_failed is True

    monkeypatch.setattr(conf, "_atomic_write_json", real_atomic_write_json)
    conf.save_config({"api_token": ""})

    assert conf._auth_persist_failed is False
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is False
    assert conf.get_api_token() == ("", False)


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
    # auth_state must still be absent — only save_config / migrate write it
    assert not os.path.exists(conf.AUTH_STATE_PATH)


def test_only_save_config_enables_persisted_flag(conf):
    conf.save_config({"api_token": "via-save"})
    assert os.path.exists(conf.AUTH_STATE_PATH)
    with open(conf.AUTH_STATE_PATH, encoding="utf-8") as f:
        assert json.load(f)["token_enabled"] is True
