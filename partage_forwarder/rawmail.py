"""Turn a raw RFC 822 message (as served by Zimbra) into a Mail."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import escape

_BODY_RE = re.compile(r'(?is)<body\b[^>]*>(.*)</body\s*>')
_STYLE_RE = re.compile(r'(?is)<style\b[^>]*>.*?</style\s*>')
_HEAD_RE = re.compile(r'(?is)<head\b[^>]*>(.*?)</head\s*>')


@dataclass
class Part:
    content_type: str
    data: bytes
    filename: str = ''
    cid: str = ''  # set on inline images the HTML refers to as cid:...


@dataclass
class Mail:
    id: str
    sender: str = ''
    reply_to: str = ''
    to: str = ''
    cc: str = ''
    subject: str = ''
    date: datetime | None = None
    message_id: str = ''
    in_reply_to: str = ''
    references: str = ''
    html: str = ''  # body fragment, ready to embed in another document
    text: str = ''
    inline: list = field(default_factory=list)       # [Part] with cid
    attachments: list = field(default_factory=list)  # [Part] with filename


def looks_like_rfc822(raw: bytes) -> bool:
    """True if raw parses as a message with real headers (not a login/error page)."""
    head = raw[:2048].lstrip().lower()
    if not head or head.startswith((b'<!doctype', b'<html', b'<?xml', b'{')):
        return False
    msg = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return any(msg[h] is not None for h in ('From', 'Subject', 'Date', 'Message-ID'))


def _embeddable(html: str) -> str:
    """The inside of <body>, plus any <style> from <head>, so the original can
    sit under our banner without its styling falling away."""
    m = _BODY_RE.search(html)
    if not m:
        return html
    head = _HEAD_RE.search(html)
    styles = ''.join(_STYLE_RE.findall(head.group(1))) if head else ''
    return styles + m.group(1)


def _text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        # Unknown or mislabelled charset: show what we can rather than drop it.
        return (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')


def _unflow(text: str, delsp: bool) -> str:
    """Undo format=flowed (RFC 3676): a line ending in a space continues on
    the next one. Without this the text arrives hard-wrapped at ~72 columns."""
    out, pending = [], ''
    for line in text.replace('\r\n', '\n').split('\n'):
        if line.startswith(' '):
            line = line[1:]  # space-stuffing
        if line.endswith(' ') and line != '-- ':
            pending += line[:-1] if delsp else line
        else:
            out.append(pending + line)
            pending = ''
    if pending:
        out.append(pending)
    return '\n'.join(out)


def _plain(part) -> str:
    text = _text(part)
    if (part.get_param('format') or '').lower() == 'flowed':
        text = _unflow(text, (part.get_param('delsp') or '').lower() == 'yes')
    return text


def _leaves(part):
    """Every non-multipart part, without descending into attached emails
    (those are forwarded whole, as .eml attachments)."""
    if part.is_multipart() and part.get_content_maintype() == 'multipart':
        for sub in part.iter_parts():
            yield from _leaves(sub)
    else:
        yield part


def _header(msg, name: str) -> str:
    try:
        value = msg[name]
    except Exception:
        return ''  # a header too broken to parse is no worse than a missing one
    return str(value).strip() if value is not None else ''


def _date(msg) -> datetime | None:
    try:
        value = msg['Date']
        return value.datetime if value is not None else None
    except Exception:
        return None


def _safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r'[\x00-\x1f/\\]', '_', (name or '').strip())
    return name[:200] or fallback


def parse(raw: bytes, msg_id: str) -> Mail:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)

    html_part = msg.get_body(preferencelist=('html',))
    text_part = msg.get_body(preferencelist=('plain',))
    html = _embeddable(_text(html_part)) if html_part else ''
    text = _plain(text_part) if text_part else ''

    inline, attachments = [], []
    for part in _leaves(msg):
        if part is html_part or part is text_part:
            continue
        ctype = part.get_content_type()
        if ctype == 'message/rfc822':
            inner = part.get_payload(0)
            data = inner.as_bytes()
            fallback = _safe_filename(_header(inner, 'Subject'), 'message') + '.eml'
        else:
            data = part.get_payload(decode=True)
            fallback = 'invite.ics' if ctype == 'text/calendar' else 'attachment'
        if not data:
            continue

        cid = (part.get('Content-ID') or '').strip().strip('<>')
        # Only keep a cid image inline if the HTML actually references it;
        # otherwise it would be invisible, so ship it as an attachment.
        if cid and f'cid:{cid}' in html:
            inline.append(Part(ctype, data, cid=cid))
        else:
            attachments.append(Part(ctype, data, filename=_safe_filename(part.get_filename(), fallback)))

    if not html and text:
        html = f'<pre style="white-space:pre-wrap;font-family:inherit;margin:0">{escape(text)}</pre>'

    return Mail(
        id=msg_id,
        sender=_header(msg, 'From'),
        reply_to=_header(msg, 'Reply-To'),
        to=_header(msg, 'To'),
        cc=_header(msg, 'Cc'),
        subject=_header(msg, 'Subject') or '(no subject)',
        date=_date(msg),
        message_id=_header(msg, 'Message-ID'),
        in_reply_to=_header(msg, 'In-Reply-To'),
        references=_header(msg, 'References'),
        html=html,
        text=text,
        inline=inline,
        attachments=attachments,
    )
