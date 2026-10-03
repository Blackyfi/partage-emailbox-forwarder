from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

from partage_forwarder.rawmail import looks_like_rfc822, parse


def make_raw(**kw) -> bytes:
    msg = EmailMessage()
    msg['From'] = kw.get('sender', 'Marie Dupont <marie.dupont@example.fr>')
    msg['To'] = 'labo-info@listes.example.fr'
    msg['Subject'] = kw.get('subject', 'Re: [labo-info] Seminar next week')
    msg['Date'] = format_datetime(datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc))
    msg['Message-ID'] = '<abc@example.fr>'
    msg['In-Reply-To'] = '<parent@example.fr>'
    msg.set_content(kw.get('text', 'Dear students,\n\nHappy Speed Dating!'))
    if 'html' in kw:
        msg.add_alternative(kw['html'], subtype='html')
    for att in kw.get('attachments', []):
        msg.add_attachment(*att[:1], maintype='application', subtype='pdf', filename=att[1])
    return msg.as_bytes()


def test_headers_and_bodies():
    mail = parse(make_raw(html='<html><head><style>p{color:red}</style></head>'
                               '<body><p>Dear students,</p></body></html>'), 'msg:1')
    assert mail.sender == 'Marie Dupont <marie.dupont@example.fr>'
    assert mail.subject == 'Re: [labo-info] Seminar next week'
    assert mail.date == datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc)
    assert mail.message_id == '<abc@example.fr>'
    assert mail.in_reply_to == '<parent@example.fr>'
    assert 'Happy Speed Dating!' in mail.text
    # The body fragment keeps the original <style> but drops the outer document.
    assert mail.html == '<style>p{color:red}</style><p>Dear students,</p>'


def test_plain_text_only_mail_gets_an_html_rendering():
    mail = parse(make_raw(text='a < b\nline two'), 'msg:1')
    assert '<pre' in mail.html and 'a &lt; b' in mail.html


def test_attachments_and_embedded_messages():
    inner = EmailMessage()
    inner['Subject'] = 'Original notice'
    inner['From'] = 'x@example.fr'
    inner.set_content('inner body')

    outer = EmailMessage()
    outer['From'] = 'a@example.fr'
    outer['Subject'] = 'Fwd'
    outer.set_content('see attached')
    outer.add_attachment(b'%PDF-1.4', maintype='application', subtype='pdf', filename='planning.pdf')
    outer.add_attachment(inner)

    mail = parse(outer.as_bytes(), 'msg:1')
    names = [a.filename for a in mail.attachments]
    # The embedded mail is one .eml attachment, not also exploded into parts.
    assert names == ['planning.pdf', 'Original notice.eml']
    assert b'inner body' in mail.attachments[1].data


def test_inline_images_only_when_referenced():
    msg = EmailMessage()
    msg['From'] = 'a@example.fr'
    msg['Subject'] = 'logo'
    msg.set_content('text')
    msg.add_alternative('<p><img src="cid:logo@x"></p>', subtype='html')
    msg.get_payload()[1].add_related(b'PNG', 'image', 'png', cid='<logo@x>')
    msg.get_payload()[1].add_related(b'GIF', 'image', 'gif', cid='<unused@x>')

    mail = parse(msg.as_bytes(), 'msg:1')
    assert [p.cid for p in mail.inline] == ['logo@x']
    assert [p.content_type for p in mail.attachments] == ['image/gif']


def test_bad_charset_does_not_crash():
    raw = (b'From: a@example.fr\r\nSubject: x\r\nContent-Type: text/plain; charset=bogus-8\r\n\r\n'
           b'caf\xc3\xa9\r\n')
    assert parse(raw, 'msg:1').text.strip() == 'café'


def test_looks_like_rfc822():
    assert looks_like_rfc822(make_raw())
    assert not looks_like_rfc822(b'<!DOCTYPE html><html>login</html>')
    assert not looks_like_rfc822(b'{"m": []}')
    assert not looks_like_rfc822(b'')


def test_format_flowed_is_unwrapped():
    raw = (b'From: a@example.fr\r\nSubject: x\r\n'
           b'Content-Type: text/plain; charset=utf-8; format=flowed\r\n\r\n'
           b'As you know, there is an international \r\nSpeed Dating session.\r\n'
           b'\r\n-- \r\nMarie\r\n')
    assert parse(raw, 'msg:1').text == (
        'As you know, there is an international Speed Dating session.\n\n-- \nMarie\n')
