# partage-emailbox-forwarder

Forwards new mail from a Partage (Zimbra) mailbox at Bordeaux INP to Gmail, with the
full message: formatted body, inline images, attachments, and who it was sent to.

It logs in through Bordeaux INP's CAS single sign-on, downloads every new message as
the raw original via Zimbra's REST API, and sends it on through Gmail's SMTP server.
There's no browser involved, so it runs comfortably on a Raspberry Pi in about 25 MB of RAM.

## What a forwarded mail looks like

- **Inbox list:** shows `Marie Dupont via Partage` and `[Partage] Re: [labo-info] Seminar…`
- **Top of the mail:** a small banner with the original From / To / Cc / Date and a list of attachments
- **Below the banner:** the original message as it appears in Partage, including images and quoted replies
- **Reply:** goes to the original author (or to the list, if the list asks for that)
- **Threads:** replies in the same conversation are grouped together, as in Partage
- **Large attachments:** if a message is over Gmail's 25 MB limit, the largest attachments
  are left out and named in the banner so you know to open the mail in Partage

## Setup

You'll need Docker (with Compose) and a Gmail account with an
[App Password](https://support.google.com/accounts/answer/185833), which requires 2-Step Verification.

```bash
git clone <repository-url>
cd partage-emailbox-forwarder
cp .env.example .env    # then fill it in
docker compose up -d --build
```

### Configuration (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `PARTAGE_USERNAME` | yes | Your CAS login, not an email address (e.g. `jdoe`) |
| `PARTAGE_PASSWORD` | yes | Your CAS password. Wrap it in single quotes if it contains `$`, `#` or spaces |
| `FORWARD_TO` | yes | Where to forward mail |
| `GMAIL_USER` | yes | Gmail account that sends the forwards |
| `GMAIL_APP_PASSWORD` | yes | App Password for `GMAIL_USER` (spaces are fine) |
| `PARTAGE_URL` | | Webmail URL. Default: `https://partage.bordeaux-inp.fr/mail` |
| `PARTAGE_QUERY` | | Zimbra search for which mail to forward. Default: `in:inbox`. For example, `in:inbox OR in:Listes` |
| `POLL_INTERVAL_SECONDS` | | How often to check. Default: `300`, minimum `30` |
| `SUBJECT_PREFIX` | | Added to forwarded subjects. Default: `[Partage]`. Set it empty for none |
| `MAX_ATTEMPTS` | | Polls to retry a failing message before giving up. Default: `5` |
| `HTTP_TIMEOUT_SECONDS` | | Timeout for each request to Partage. Default: `30` |
| `LOG_LEVEL` | | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Default: `INFO` |
| `DB_PATH` | | SQLite database. Default: `/data/emails.db` |

## Day to day

```bash
docker compose logs -f                                         # what it's doing
docker compose ps                                              # "healthy" = polls are succeeding
docker compose exec forwarder python -m partage_forwarder --status   # recently forwarded mail
```

Logs stay quiet while there is nothing new. Each forwarded mail gets one line. To also
log every empty poll, set `LOG_LEVEL=DEBUG`.

To preview what would be sent without sending anything or marking anything as done:

```bash
docker compose run --rm forwarder python -m partage_forwarder --dry-run /data/preview
# then open data/preview/*.eml in any mail client
```

## How it decides what to forward

- **Every poll:** it lists the messages matching `PARTAGE_QUERY` from the last few days and
  forwards any it hasn't forwarded before. This includes mail you've already read in Partage.
  Nothing in Partage is changed, and messages are not marked as read.
- **First run:** messages already in the mailbox count as done, except unread ones from the
  past week. A new install therefore doesn't flood your inbox with old mail. A database from
  the older, browser-based version is recognised, so nothing it already sent is sent again.
- **Downtime:** after an outage, it looks back far enough (up to 60 days) to catch up on
  anything that arrived in the meantime.
- **Failures:** a message that fails is retried on later polls. After `MAX_ATTEMPTS` failures
  it is skipped, and you get a short "could not forward" email with its sender and subject.
  Connection problems (Partage or Gmail down, Wi-Fi gone) don't count as attempts. The
  forwarder just backs off and tries again.
- **Wrong password:** if CAS rejects the password, the forwarder pauses for 6 hours instead
  of retrying every few minutes, which could get the account locked. Fix `.env` and run
  `docker compose up -d` to retry right away.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
set -a; . ./.env; set +a; DB_PATH=./data/dev.db python -m partage_forwarder --dry-run ./preview
```

```
partage_forwarder/
├── __main__.py   # CLI, poll loop, backoff, health check
├── config.py     # environment variables -> Config
├── partage.py    # CAS/Shibboleth login and Zimbra REST client
├── rawmail.py    # raw RFC 822 -> Mail (bodies, inline images, attachments)
├── mailer.py     # the forwarded message, and Gmail SMTP
├── service.py    # one poll: what's new, forward it, remember it
└── store.py      # SQLite: what has been forwarded
tests/            # pytest suite, no network needed
```

## License

MIT
