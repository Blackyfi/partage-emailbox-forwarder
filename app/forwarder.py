import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, parseaddr
from html import unescape

FROM_NAME = 'Partage Auto-Forwarder'
SUBJECT_PREFIX = '[Partage]'


def _html_to_text(html: str) -> str:
    """Best-effort plain-text rendering of the original HTML body."""
    text = re.sub(r'(?is)<(script|style)\b.*?</\1>', '', html)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|h[1-6])\s*>', '\n\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = unescape(text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def forward(email: dict, cfg: dict):
    subject = email.get('subject') or '(no subject)'
    sender = (email.get('from') or 'unknown sender').strip()
    date = email.get('date') or ''
    body_html = email.get('body') or ''

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'{SUBJECT_PREFIX} {subject}'
    msg['From'] = formataddr((FROM_NAME, cfg['gmail_user']))
    msg['To'] = cfg['forward_to']
    msg['Date'] = formatdate(localtime=True)
    msg['X-Forwarded-For-Mailbox'] = 'partage.bordeaux-inp.fr'

    # Let a reply go to the person who actually wrote, when we scraped a real
    # address rather than just a display name.
    reply_name, reply_addr = parseaddr(sender)
    if '@' in reply_addr:
        # Assigning the raw "Name <addr>" string makes the compat32 generator
        # RFC2047-encode the whole value, address included, leaving no
        # addr-spec to reply to. formataddr encodes only the display name.
        msg['Reply-To'] = formataddr((reply_name, reply_addr))

    text_part = (
        f'Forwarded automatically from your Partage mailbox\n'
        f'{"-" * 48}\n'
        f'From:    {sender}\n'
        f'Date:    {date}\n'
        f'Subject: {subject}\n'
        f'{"-" * 48}\n\n'
        f'{_html_to_text(body_html)}\n'
    )

    html_part = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
  <div style="border-left:3px solid #4a6fa5;background:#f4f6f9;padding:10px 14px;
              margin-bottom:18px;font-size:13px;color:#33475b">
    <div style="font-weight:600;color:#4a6fa5;margin-bottom:6px">
      Forwarded automatically from your Partage mailbox
    </div>
    <div><strong>From:</strong> {sender}</div>
    <div><strong>Date:</strong> {date}</div>
    <div><strong>Subject:</strong> {subject}</div>
  </div>
  {body_html}
</div>"""

    msg.attach(MIMEText(text_part, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_part, 'html', 'utf-8'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(cfg['gmail_user'], cfg['gmail_password'])
        server.sendmail(cfg['gmail_user'], cfg['forward_to'], msg.as_string())
