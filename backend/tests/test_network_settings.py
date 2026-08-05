from flask import Flask
import pytest


@pytest.fixture()
def network_settings_client(monkeypatch):
    import routes.settings as settings_mod

    state = {
        'debug_mode': True,
        'lan_mode': False,
    }

    def load_config():
        return dict(state), None

    def save_config(cfg):
        state.clear()
        state.update(cfg)

    monkeypatch.setattr(settings_mod, 'load_config', load_config)
    monkeypatch.setattr(settings_mod, 'save_config', save_config)

    app = Flask(__name__)
    app.register_blueprint(settings_mod.settings_bp, url_prefix='/api')
    app.config['TESTING'] = True
    return app.test_client(), state


def test_get_network_settings_never_returns_token(network_settings_client):
    client, state = network_settings_client
    state['lan_mode'] = True
    state['api_token'] = 'this-secret-must-not-leak'

    response = client.get('/api/settings/network')

    assert response.status_code == 200
    assert response.get_json() == {
        'lan_mode': True,
        'api_token_configured': True,
        'restart_required': False,
    }
    assert b'this-secret-must-not-leak' not in response.data


def test_enable_lan_allows_empty_api_token(network_settings_client):
    client, state = network_settings_client

    response = client.put('/api/settings/network', json={'lan_mode': True})

    assert response.status_code == 200
    assert response.get_json()['api_token_configured'] is False
    assert response.get_json()['restart_required'] is True
    assert state == {'debug_mode': True, 'lan_mode': True}


def test_enable_lan_saves_token_without_echoing_it(network_settings_client):
    client, state = network_settings_client
    token = '0123456789abcdef0123456789abcdef'

    response = client.put('/api/settings/network', json={
        'lan_mode': True,
        'api_token': token,
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['lan_mode'] is True
    assert payload['api_token_configured'] is True
    assert payload['restart_required'] is True
    assert '重启' in payload['message']
    assert 'api_token' not in payload
    assert token.encode() not in response.data
    assert state == {
        'debug_mode': True,
        'lan_mode': True,
        'api_token': token,
    }


def test_existing_token_is_preserved_when_field_is_omitted(network_settings_client):
    client, state = network_settings_client
    state.update({
        'lan_mode': True,
        'api_token': 'existing-token-0123456789',
    })

    response = client.put('/api/settings/network', json={'lan_mode': False})

    assert response.status_code == 200
    assert response.get_json()['restart_required'] is True
    assert state['lan_mode'] is False
    assert state['api_token'] == 'existing-token-0123456789'


def test_empty_api_token_explicitly_clears_existing_token(network_settings_client):
    client, state = network_settings_client
    state.update({
        'lan_mode': True,
        'api_token': 'existing-token-0123456789',
    })

    response = client.put('/api/settings/network', json={
        'lan_mode': True,
        'api_token': '',
    })

    assert response.status_code == 200
    assert response.get_json()['api_token_configured'] is False
    assert response.get_json()['restart_required'] is False
    assert state['lan_mode'] is True
    assert state['api_token'] == ''


@pytest.mark.parametrize('payload', [
    {},
    {'lan_mode': 1},
    {'lan_mode': True, 'api_token': 123},
    {'lan_mode': True, 'api_token': 'too-short'},
    {'lan_mode': True, 'api_token': 'valid-length-token\nwith-newline'},
])
def test_network_settings_reject_invalid_payloads(network_settings_client, payload):
    client, state = network_settings_client
    before = dict(state)

    response = client.put('/api/settings/network', json=payload)

    assert response.status_code == 400
    assert state == before


def test_network_settings_refuse_to_overwrite_unreadable_config(
    network_settings_client,
    monkeypatch,
):
    client, state = network_settings_client
    import routes.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        'load_config',
        lambda: ({'lan_mode': False}, 'config is corrupt'),
    )

    get_response = client.get('/api/settings/network')
    put_response = client.put('/api/settings/network', json={'lan_mode': False})

    assert get_response.status_code == 503
    assert put_response.status_code == 503
    assert state == {'debug_mode': True, 'lan_mode': False}


def test_real_app_guard_allows_empty_token_and_protects_configured_token(monkeypatch):
    import app as app_mod
    import config
    import routes.settings as settings_mod

    state = {
        'lan_mode': True,
        'api_token': 'existing-token-0123456789',
    }

    monkeypatch.setattr(settings_mod, 'load_config', lambda: (dict(state), None))

    def save_config(cfg):
        state.clear()
        state.update(cfg)

    monkeypatch.setattr(settings_mod, 'save_config', save_config)
    monkeypatch.setattr(
        config,
        'get_api_token',
        lambda: (str(state.get('api_token') or ''), False),
    )

    client = app_mod.app.test_client()
    unauthenticated = client.put('/api/settings/network', json={
        'lan_mode': True,
        'api_token': '',
    })
    authenticated = client.put(
        '/api/settings/network',
        json={'lan_mode': True, 'api_token': ''},
        headers={'X-API-Token': 'existing-token-0123456789'},
    )
    open_after_clear = client.put('/api/settings/network', json={
        'lan_mode': False,
    })

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.get_json()['api_token_configured'] is False
    assert open_after_clear.status_code == 200
    assert state['lan_mode'] is False
    assert state['api_token'] == ''
