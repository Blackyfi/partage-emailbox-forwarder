import pytest

from partage_forwarder.config import load

BASE_ENV = {
    'PARTAGE_USERNAME': 'jdoe',
    'PARTAGE_PASSWORD': 's3cret',
    'FORWARD_TO': 'me@gmail.com',
    'GMAIL_USER': 'bot@gmail.com',
    'GMAIL_APP_PASSWORD': 'abcd efgh ijkl mnop',
    'DB_PATH': ':memory:',
}


@pytest.fixture
def cfg():
    return load(dict(BASE_ENV))
