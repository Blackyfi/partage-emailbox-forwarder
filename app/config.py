import os
import sys


def load():
    required = [
        'PARTAGE_USERNAME',
        'PARTAGE_PASSWORD',
        'FORWARD_TO',
        'GMAIL_USER',
        'GMAIL_APP_PASSWORD',
    ]
    # A blank value is as broken as an absent one, and fails far less clearly
    # later (an empty password just looks like a rejected login).
    missing = [k for k in required if not os.environ.get(k, '').strip()]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    cfg = {
        'username':        os.environ['PARTAGE_USERNAME'],
        'password':        os.environ['PARTAGE_PASSWORD'],
        'partage_url':     os.environ.get('PARTAGE_URL', 'https://partage.bordeaux-inp.fr/mail'),
        'cas_url':         os.environ.get('CAS_URL', ''),  # unused: login goes via partage_url
        'forward_to':      os.environ['FORWARD_TO'],
        'gmail_user':      os.environ['GMAIL_USER'],
        'gmail_password':  os.environ['GMAIL_APP_PASSWORD'].replace(' ', ''),
        'poll_interval':   int(os.environ.get('POLL_INTERVAL_SECONDS', 300)),
        'log_level':       os.environ.get('LOG_LEVEL', 'INFO'),
        'db_path':         os.environ.get('DB_PATH', '/data/emails.db'),
        'browser_timeout': int(os.environ.get('BROWSER_TIMEOUT_MS', 20000)),
    }
    return cfg
