import pytest

from nkfix_mcp.config import Config, ConfigError
from tests.helpers import API_KEY


def read(**env: str) -> Config:
    return Config.from_env(env)


def test_without_url_aborts_with_clear_message():
    with pytest.raises(ConfigError, match='NKF_URL'):
        read(NKF_API_KEY=API_KEY)


@pytest.mark.parametrize('url', ['127.0.0.1:6062', 'ftp://nas/nkf', 'http://'])
def test_url_must_be_http_or_https(url):
    with pytest.raises(ConfigError, match='NKF_URL'):
        read(NKF_URL=url)


def test_trailing_slash_is_dropped():
    assert read(NKF_URL='http://nas:6060/').url == 'http://nas:6060'


def test_api_key_without_prefix_is_rejected_without_showing_the_value():
    with pytest.raises(ConfigError) as error:
        read(NKF_URL='http://x', NKF_API_KEY='geheim-123')
    assert 'nkf_' in str(error.value)
    assert 'geheim-123' not in str(error.value)


@pytest.mark.parametrize('variable', ['NKF_API_KEY', 'NKF_URL', 'NKF_PASSWORD'])
def test_unresolved_variable_from_mcp_json_is_detected(variable):
    env = {'NKF_URL': 'http://x', 'NKF_USERNAME': 'anna', 'NKF_PASSWORD': 'pw-1234567890'}
    env[variable] = '${' + variable + '}'
    with pytest.raises(ConfigError, match='nicht aufgelöst'):
        Config.from_env(env)


def test_username_without_password_is_an_error():
    with pytest.raises(ConfigError, match='NKF_PASSWORD'):
        read(NKF_URL='http://x', NKF_USERNAME='anna')


def test_api_key_wins_over_username_and_password():
    config = read(NKF_URL='http://x', NKF_API_KEY=API_KEY, NKF_USERNAME='anna', NKF_PASSWORD='pw-1234567890')
    assert config.auth_method == 'schluessel'


def test_username_and_password_give_a_session():
    assert read(NKF_URL='http://x', NKF_USERNAME='anna', NKF_PASSWORD='pw-1234567890').auth_method == 'sitzung'


def test_without_credentials_access_is_open():
    assert read(NKF_URL='http://x').auth_method == 'offen'


def test_empty_values_count_as_not_set():
    assert read(NKF_URL='http://x', NKF_API_KEY='  ', NKF_USERNAME='').auth_method == 'offen'


def test_secrets_are_not_in_the_representation():
    config = read(NKF_URL='http://x', NKF_API_KEY=API_KEY, NKF_USERNAME='anna', NKF_PASSWORD='pw-1234567890')
    text = repr(config) + str(config)
    assert API_KEY not in text
    assert 'pw-1234567890' not in text


def test_receipt_dir_must_be_an_existing_directory(tmp_path):
    assert read(NKF_URL='http://x', NKF_RECEIPT_DIR=str(tmp_path)).receipt_dir == tmp_path.resolve()
    with pytest.raises(ConfigError, match='NKF_RECEIPT_DIR'):
        read(NKF_URL='http://x', NKF_RECEIPT_DIR=str(tmp_path / 'fehlt'))


@pytest.mark.parametrize(('value', 'expected'), [(None, True), ('0', True), ('1', False), ('ja', False)])
def test_confirm_on_record_is_the_default(value, expected):
    env = {'NKF_URL': 'http://x'}
    if value is not None:
        env['NKF_NO_RECORD_PROMPT'] = value
    assert Config.from_env(env).confirm_on_record is expected


@pytest.mark.parametrize(('url', 'local'), [
    ('http://127.0.0.1:6062', True), ('http://localhost:6060', True), ('http://[::1]:6060', True),
    ('http://nas.local:6060', False), ('https://nkf.example.org', False), ('http://192.168.1.20:6060', False),
])
def test_local_address_is_detected(url, local):
    assert read(NKF_URL=url).is_local is local


def test_credentials_in_the_url_are_rejected_without_naming_them():
    with pytest.raises(ConfigError) as error:
        read(NKF_URL='https://max:Geheim99@nkf.example.org')
    assert 'Geheim99' not in str(error.value)
    assert 'Zugangsdaten' in str(error.value)
