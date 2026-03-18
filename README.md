# partage-emailbox-forwarder

Automated email forwarder that monitors a Partage (Zimbra) mailbox via CAS authentication and forwards new messages to Gmail using Playwright for browser automation.

## Features

- **Automatic polling** – Checks for new emails at configurable intervals
- **CAS authentication** – Logs into Partage via Bordeaux INP's central authentication system
- **Email forwarding** – Forwards new emails to a specified Gmail address with HTML body preservation
- **Duplicate prevention** – Tracks already-forwarded messages in SQLite database
- **Docker-ready** – Includes Dockerfile and docker-compose.yml for easy deployment

## Requirements

- Python 3.8+
- Playwright (browser automation)
- Docker & Docker Compose (optional, for containerized deployment)

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd partage-emailbox-forwarder
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|----------|-------------|
| `PARTAGE_USERNAME` | Your Partage email address (e.g., `prenom.nom@bordeaux-inp.fr`) |
| `PARTAGE_PASSWORD` | Your Partage/CAS password |
| `CAS_URL` | CAS login URL |
| `FORWARD_TO` | Destination Gmail address |
| `GMAIL_USER` | Gmail sender address |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your regular password) |

Optional variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PARTAGE_URL` | `https://partage.bordeaux-inp.fr/mail` | Partage webmail URL |
| `POLL_INTERVAL_SECONDS` | `300` | Check interval in seconds |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `DB_PATH` | `/data/emails.db` | SQLite database path |
| `BROWSER_TIMEOUT_MS` | `20000` | Browser operation timeout in milliseconds |

> **Note:** For Gmail, you must use an [App Password](https://support.google.com/accounts/answer/185833) instead of your regular password. Enable 2-Step Verification on your Google account first.

## Usage

### Running directly with Python

```bash
python app/main.py
```

### Running with Docker

```bash
docker-compose up -d
```

View logs:

```bash
docker-compose logs -f forwarder
```

Stop the service:

```bash
docker-compose down
```

## How it works

1. **Login** – The script uses Playwright to launch a headless Chromium browser and authenticate via CAS
2. **Poll inbox** – Navigates to Partage webmail and checks for new emails since the last poll
3. **Forward** – For each new email, sends a formatted message to Gmail via SMTP
4. **Track** – Stores forwarded email IDs in SQLite to prevent duplicates
5. **Repeat** – Continues polling at the configured interval

## Project structure

```
├── app/
│   ├── main.py        # Main entry point and run loop
│   ├── browser.py     # Playwright-based Partage session management
│   ├── forwarder.py   # Gmail SMTP forwarding logic
│   ├── config.py      # Environment variable loading
│   └── db.py          # SQLite database operations
├── data/              # Persistent data storage (SQLite DB)
├── docker-compose.yml # Docker Compose configuration
├── Dockerfile         # Container build definition
└── requirements.txt   # Python dependencies
```

## Important notes

- **CSS selectors** – The browser automation uses CSS selectors that may need adjustment if Partage's UI changes. Inspect the live DOM to verify selectors (`.zl__ri__r`, `.msg`, etc.)
- **Rate limiting** – Adjust `POLL_INTERVAL_SECONDS` to avoid overloading the server or triggering rate limits
- **Security** – Never commit your `.env` file. The credentials are already excluded via `.gitignore`

## License

MIT
