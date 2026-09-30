"""Turn a raw RFC 822 message (as served by Zimbra) into the dict the forwarder uses.

Message dict shape (shared with the DOM fallback in browser.py):
    id, from, subject, date, body (HTML), text (plain, optional),
    inline      - [{'cid', 'content_type', 'data'}]  images the HTML refers to via cid:
    attachments - [{'filename', 'content_type', 'data'}]
"""
import re
from email import policy
from email.parser import BytesParser
from html import escape

_BODY_RE = re.compile(r'(?is)<body\b[^>]*>(.*)</body\s*>')


def looks_like_rfc822(raw: bytes) -> bool:
    """True if raw parses as a message with real headers (not a login/error page)."""
    head = raw[:2048].lstrip().lower()
    if not head or head.startswith((b'<!doctype', b'<html', b'<?xml', b'{')):
        return False
    msg = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return any(msg[h] is not None for h in ('From', 'Subject', 'Date', 'Message-ID'))


def _body_fragment(html: str) -> str:
    """The inside of <body>, so it can be embedded under our banner."""
    m = _BODY_RE.search(html)
    return m.group(1) if m else html


def parse(raw: bytes, msg_id: str) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    html_part = msg.get_body(preferencelist=('html',))
    text_part = msg.get_body(preferencelist=('plain',))
    html = _body_fragment(html_part.get_content()) if html_part else ''
    text = text_part.get_content() if text_part else ''

    inline, attachments = [], []
    for part in msg.walk():
        if part.is_multipart() or part is html_part or part is text_part:
            continue
        ctype = part.get_content_type()
        # Embedded mails come through as message/rfc822 parts.
        if ctype == 'message/rfc822':
            payload = part.get_payload(0).as_bytes()
        else:
            payload = part.get_payload(decode=True)
        if not payload:
            continue

        cid = (part.get('Content-ID') or '').strip().strip('<>')
        filename = part.get_filename()
        # Only keep a cid image inline if the HTML actually references it;
        # otherwise it would be invisible, so ship it as an attachment.
        if cid and f'cid:{cid}' in html:
            inline.append({'cid': cid, 'content_type': ctype, 'data': payload})
        else:
            attachments.append({
                'filename': filename or ('message.eml' if ctype == 'message/rfc822' else 'attachment'),
                'content_type': ctype,
                'data': payload,
            })

    if not html and text:
        html = f'<pre style="white-space:pre-wrap;font-family:inherit">{escape(text)}</pre>'

    return {
        'id': msg_id,
        'from': str(msg['From'] or ''),
        'subject': str(msg['Subject'] or '(no subject)'),
        'date': str(msg['Date'] or ''),
        'body': html,
        'text': text,
        'inline': inline,
        'attachments': attachments,
    }
