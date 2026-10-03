import pytest

from partage_forwarder.config import ConfigError, load

from .conftest import BASE_ENV


def test_defaults_and_cleanup():
    cfg = load(dict(BASE_ENV))
    assert cfg.gmail_password == 'abcdefghijklmnop'
    assert cfg.partage_base == 'https://partage.bordeaux-inp.fr'
    assert cfg.query == 'in:inbox'
    assert cfg.http_timeout == 30
    assert cfg.subject_prefix == '[Partage]'


def test_missing_and_blank_values_are_reported():
    env = dict(BASE_ENV, GMAIL_USER='  ')
    del env['FORWARD_TO']
    with pytest.raises(ConfigError, match='FORWARD_TO, GMAIL_USER'):
        load(env)


def test_legacy_browser_timeout_is_honoured():
    assert load(dict(BASE_ENV, BROWSER_TIMEOUT_MS='60000')).http_timeout == 60
    assert load(dict(BASE_ENV, BROWSER_TIMEOUT_MS='60000', HTTP_TIMEOUT_SECONDS='5')).http_timeout == 5


def test_bad_numbers_are_rejected():
    with pytest.raises(ConfigError, match='POLL_INTERVAL_SECONDS'):
        load(dict(BASE_ENV, POLL_INTERVAL_SECONDS='5m'))
    with pytest.raises(ConfigError, match='at least 30'):
        load(dict(BASE_ENV, POLL_INTERVAL_SECONDS='5'))


def test_empty_subject_prefix_is_allowed():
    assert load(dict(BASE_ENV, SUBJECT_PREFIX='')).subject_prefix == ''
