import os
from dataclasses import dataclass
from urllib.parse import urlsplit

REQUIRED = (
    'PARTAGE_USERNAME',
    'PARTAGE_PASSWORD',
    'FORWARD_TO',
    'GMAIL_USER',
    'GMAIL_APP_PASSWORD',
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    username: str
    password: str
    forward_to: str
    gmail_user: str
    gmail_password: str
    partage_url: str = 'https://partage.bordeaux-inp.fr/mail'
    query: str = 'in:inbox'
    poll_interval: int = 300
    http_timeout: float = 30.0
    db_path: str = '/data/emails.db'
    log_level: str = 'INFO'
    subject_prefix: str = '[Partage]'
    max_attempts: int = 5
    smtp_host: str = 'smtp.gmail.com'
    smtp_port: int = 465

    @property
    def partage_base(self) -> str:
        """Scheme and host of the webmail, e.g. https://partage.bordeaux-inp.fr"""
        u = urlsplit(self.partage_url)
        return f'{u.scheme}://{u.netloc}'


def _int(env, name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f'{name} must be a whole number, got {raw!r}') from None
    if value < minimum:
        raise ConfigError(f'{name} must be at least {minimum}, got {value}')
    return value


def load(env=None) -> Config:
    env = os.environ if env is None else env

    # A blank value is as broken as an absent one, and fails far less clearly
    # later (an empty password just looks like a rejected login).
    missing = [k for k in REQUIRED if not env.get(k, '').strip()]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    # HTTP_TIMEOUT_SECONDS replaces the Playwright-era BROWSER_TIMEOUT_MS;
    # honour the old name so existing .env files keep working.
    if env.get('HTTP_TIMEOUT_SECONDS', '').strip():
        timeout = float(_int(env, 'HTTP_TIMEOUT_SECONDS', 30, minimum=1))
    else:
        timeout = _int(env, 'BROWSER_TIMEOUT_MS', 30000, minimum=1000) / 1000

    return Config(
        username=env['PARTAGE_USERNAME'].strip(),
        password=env['PARTAGE_PASSWORD'],
        forward_to=env['FORWARD_TO'].strip(),
        gmail_user=env['GMAIL_USER'].strip(),
        # Google displays app passwords in groups of four; people paste them so.
        gmail_password=env['GMAIL_APP_PASSWORD'].replace(' ', ''),
        partage_url=env.get('PARTAGE_URL', '').strip() or Config.partage_url,
        query=env.get('PARTAGE_QUERY', '').strip() or Config.query,
        poll_interval=_int(env, 'POLL_INTERVAL_SECONDS', Config.poll_interval, minimum=30),
        http_timeout=timeout,
        db_path=env.get('DB_PATH', '').strip() or Config.db_path,
        log_level=(env.get('LOG_LEVEL', '').strip() or Config.log_level).upper(),
        subject_prefix=env.get('SUBJECT_PREFIX', Config.subject_prefix).strip(),
        max_attempts=_int(env, 'MAX_ATTEMPTS', Config.max_attempts, minimum=1),
    )
