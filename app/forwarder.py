import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, parseaddr
from html import escape, unescape

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


def _split_type(content_type: str) -> tuple:
    maintype, _, subtype = (content_type or 'application/octet-stream').partition('/')
    return maintype or 'application', subtype or 'octet-stream'


def build_message(email: dict, cfg: dict) -> EmailMessage:
    subject = email.get('subject') or '(no subject)'
    sender = (email.get('from') or 'unknown sender').strip()
    date = email.get('date') or ''
    body_html = email.get('body') or ''
    body_text = email.get('text') or _html_to_text(body_html)

    msg = EmailMessage()
    msg['Subject'] = f'{SUBJECT_PREFIX} {subject}'
    msg['From'] = formataddr((FROM_NAME, cfg['gmail_user']))
    msg['To'] = cfg['forward_to']
    msg['Date'] = formatdate(localtime=True)
    msg['X-Forwarded-For-Mailbox'] = 'partage.bordeaux-inp.fr'

    # Let a reply go to the person who actually wrote, when we scraped a real
    # address rather than just a display name.
    reply_name, reply_addr = parseaddr(sender)
    if '@' in reply_addr:
        msg['Reply-To'] = formataddr((reply_name, reply_addr))

    text_part = (
        f'Forwarded automatically from your Partage mailbox\n'
        f'{"-" * 48}\n'
        f'From:    {sender}\n'
        f'Date:    {date}\n'
        f'Subject: {subject}\n'
        f'{"-" * 48}\n\n'
        f'{body_text}\n'
    )

    html_part = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
  <div style="border-left:3px solid #4a6fa5;background:#f4f6f9;padding:10px 14px;
              margin-bottom:18px;font-size:13px;color:#33475b">
    <div style="font-weight:600;color:#4a6fa5;margin-bottom:6px">
      Forwarded automatically from your Partage mailbox
    </div>
    <div><strong>From:</strong> {escape(sender)}</div>
    <div><strong>Date:</strong> {escape(date)}</div>
    <div><strong>Subject:</strong> {escape(subject)}</div>
  </div>
  {body_html}
</div>"""

    msg.set_content(text_part)
    msg.add_alternative(html_part, subtype='html')

    # Images the HTML refers to via cid: travel with it (multipart/related).
    html_msg = msg.get_payload()[1]
    for img in email.get('inline') or []:
        maintype, subtype = _split_type(img['content_type'])
        html_msg.add_related(img['data'], maintype, subtype, cid=f"<{img['cid']}>")

    for att in email.get('attachments') or []:
        maintype, subtype = _split_type(att['content_type'])
        msg.add_attachment(
            att['data'], maintype=maintype, subtype=subtype,
            filename=att.get('filename') or 'attachment',
        )
    return msg


def forward(email: dict, cfg: dict):
    msg = build_message(email, cfg)
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(cfg['gmail_user'], cfg['gmail_password'])
        server.send_message(msg, cfg['gmail_user'], cfg['forward_to'])
