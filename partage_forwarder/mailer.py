"""Build the forwarded message and send it through Gmail's SMTP server."""
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, formatdate, getaddresses, make_msgid
from html import escape, unescape

from .rawmail import Mail

log = logging.getLogger(__name__)

# Gmail refuses messages over 25 MB, measured after base64 inflates the
# attachments by about a third. Leave headroom for headers and the banner.
MAX_ENCODED_BYTES = 24 * 1000 * 1000
ACCENT = '#4a6fa5'


def _encoded_size(n: int) -> int:
    return n * 4 // 3 + n // 38  # base64 plus its line breaks


def _human_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB'):
        if n < 1024 or unit == 'MB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def html_to_text(html: str) -> str:
    """Best-effort plain-text rendering of an HTML body."""
    text = re.sub(r'(?is)<(script|style|head)\b.*?</\1\s*>', '', html)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6]|blockquote)\s*>', '\n\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _split_type(content_type: str) -> tuple:
    maintype, _, subtype = (content_type or 'application/octet-stream').partition('/')
    return maintype or 'application', subtype or 'octet-stream'


def _display_name(header: str) -> str:
    """'Marie Dupont <m.d@example.fr>' -> 'Marie Dupont'; bare address if no name."""
    pairs = getaddresses([header]) if header else []
    if not pairs:
        return ''
    name, addr = pairs[0]
    return (name or addr).strip()


def _shorten(value: str, limit: int = 300) -> str:
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip(', ') + '…'


def _addresses_html(header: str) -> str:
    """Address list with clickable mailto: links."""
    out = []
    for name, addr in getaddresses([header]):
        if not addr:
            continue
        link = f'<a href="mailto:{escape(addr)}" style="color:{ACCENT};text-decoration:none">{escape(addr)}</a>'
        out.append(f'{escape(name)} &lt;{link}&gt;' if name else link)
    return ', '.join(out) or escape(header)


def _fit_attachments(mail: Mail) -> tuple:
    """Split attachments into (kept, dropped) so the message stays under
    Gmail's size limit, dropping the largest ones first."""
    budget = MAX_ENCODED_BYTES - _encoded_size(len(mail.html.encode()) + len(mail.text.encode()))
    budget -= sum(_encoded_size(len(p.data)) for p in mail.inline)
    dropped = set()
    total = sum(_encoded_size(len(a.data)) for a in mail.attachments)
    for i in sorted(range(len(mail.attachments)), key=lambda i: -len(mail.attachments[i].data)):
        if total <= budget:
            break
        dropped.add(i)
        total -= _encoded_size(len(mail.attachments[i].data))
    kept = [a for i, a in enumerate(mail.attachments) if i not in dropped]
    gone = [a for i, a in enumerate(mail.attachments) if i in dropped]
    return kept, gone


def _banner_rows(mail: Mail, kept: list, dropped: list) -> list:
    """(label, html, text) for each line of the 'forwarded from' banner."""
    rows = [('From', _addresses_html(mail.sender), mail.sender)]
    if mail.to:
        rows.append(('To', _addresses_html(_shorten(mail.to)), _shorten(mail.to)))
    if mail.cc:
        rows.append(('Cc', _addresses_html(_shorten(mail.cc)), _shorten(mail.cc)))
    if mail.date:
        when = mail.date.astimezone().strftime('%a %d %b %Y, %H:%M')
        rows.append(('Date', escape(when), when))
    if kept:
        names = ', '.join(f'{a.filename} ({_human_size(len(a.data))})' for a in kept)
        rows.append(('Attached', escape(names), names))
    if dropped:
        names = ', '.join(f'{a.filename} ({_human_size(len(a.data))})' for a in dropped)
        rows.append(('Too large', escape(names) + ' &mdash; open the original in Partage',
                     f'{names} - open the original in Partage'))
    return rows


def build_message(mail: Mail, cfg) -> EmailMessage:
    kept, dropped = _fit_attachments(mail)
    rows = _banner_rows(mail, kept, dropped)
    sender_name = _display_name(mail.sender) or 'Unknown sender'

    msg = EmailMessage()
    msg['Subject'] = f'{cfg.subject_prefix} {mail.subject}' if cfg.subject_prefix else mail.subject
    # Show who actually wrote in the inbox list, while sending from our own
    # address (Gmail rewrites any From it does not own anyway).
    msg['From'] = formataddr((f'{sender_name} via Partage', cfg.gmail_user))
    msg['To'] = cfg.forward_to
    # Hitting "Reply" should reach the author (or the list, if it asks so).
    msg['Reply-To'] = mail.reply_to or mail.sender or cfg.gmail_user
    msg['Date'] = format_datetime(mail.date) if mail.date else formatdate(localtime=True)
    # Reusing the original Message-ID and threading headers lets Gmail group
    # a forwarded conversation into one thread, just as Partage does.
    msg['Message-ID'] = mail.message_id or make_msgid(domain='partage-forwarder')
    if mail.in_reply_to:
        msg['In-Reply-To'] = mail.in_reply_to
    if mail.references:
        msg['References'] = mail.references
    msg['X-Partage-Forwarder'] = mail.id
    msg['Auto-Submitted'] = 'auto-generated'

    text_banner = '\n'.join(f'{label + ":":<10} {text}' for label, _, text in rows)
    body_text = mail.text or html_to_text(mail.html)
    msg.set_content(
        f'---------- Forwarded from Partage ----------\n'
        f'{text_banner}\n\n'
        f'{body_text}\n'
    )

    row_html = ''.join(
        f'<tr><td style="color:#5f6368;padding:1px 14px 1px 0;vertical-align:top;'
        f'white-space:nowrap">{label}</td><td style="padding:1px 0">{value}</td></tr>'
        for label, value, _ in rows
    )
    msg.add_alternative(f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<div style="font:13px/1.5 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
            color:#3c4043;background:#f6f8fb;border:1px solid #e1e6ee;
            border-left:4px solid {ACCENT};border-radius:6px;
            padding:10px 14px;margin:0 0 20px">
  <div style="font-size:11px;font-weight:600;letter-spacing:.05em;
              text-transform:uppercase;color:{ACCENT};margin-bottom:4px">
    <a href="{escape(cfg.partage_url)}" style="color:{ACCENT};text-decoration:none">Forwarded from Partage</a>
  </div>
  <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px">{row_html}</table>
</div>
<div>{mail.html}</div>
</body></html>""", subtype='html')

    # Images the HTML refers to via cid: travel with it (multipart/related).
    html_part = msg.get_payload()[1]
    for img in mail.inline:
        maintype, subtype = _split_type(img.content_type)
        html_part.add_related(img.data, maintype, subtype, cid=f'<{img.cid}>')

    for att in kept:
        if att.content_type == 'message/rfc822':
            # EmailMessage wants a message object for message/rfc822 parts;
            # shipping the bytes as a generic file keeps them byte-exact.
            maintype, subtype = 'application', 'octet-stream'
        else:
            maintype, subtype = _split_type(att.content_type)
        msg.add_attachment(att.data, maintype=maintype, subtype=subtype, filename=att.filename)

    if dropped:
        log.warning('Left out %d attachment(s) too large for Gmail: %s',
                    len(dropped), ', '.join(a.filename for a in dropped))
    return msg


def build_failure_notice(summary, error: Exception, cfg) -> EmailMessage:
    """A short heads-up for a message that could not be forwarded at all."""
    msg = EmailMessage()
    subject = summary.subject or '(no subject)'
    msg['Subject'] = f'{cfg.subject_prefix} Could not forward: {subject}'.strip()
    msg['From'] = formataddr(('Partage forwarder', cfg.gmail_user))
    msg['To'] = cfg.forward_to
    msg['Date'] = formatdate(localtime=True)
    msg['Auto-Submitted'] = 'auto-generated'
    msg.set_content(
        f'A message in your Partage mailbox could not be forwarded after '
        f'{cfg.max_attempts} attempts, so it will not be retried.\n\n'
        f'From:    {summary.sender}\n'
        f'Subject: {subject}\n'
        f'Error:   {type(error).__name__}: {error}\n\n'
        f'Read it in Partage: {cfg.partage_url}\n'
    )
    return msg


class Mailer:
    """One SMTP connection, opened on first use and shared by a whole batch."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._smtp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        if self._smtp is None:
            smtp = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=60)
            try:
                smtp.login(self.cfg.gmail_user, self.cfg.gmail_password)
            except Exception:
                smtp.close()
                raise
            self._smtp = smtp

    def send(self, msg: EmailMessage):
        self.connect()
        self._smtp.send_message(msg, self.cfg.gmail_user, [self.cfg.forward_to])

    def close(self):
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except Exception:
                pass
            self._smtp = None
