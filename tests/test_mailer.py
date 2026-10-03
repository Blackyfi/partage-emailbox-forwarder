from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser

from partage_forwarder import mailer
from partage_forwarder.mailer import build_message, html_to_text
from partage_forwarder.rawmail import Mail, Part


def sample(**kw) -> Mail:
    defaults = dict(
        id='msg:12463',
        sender='Marie Dupont <marie.dupont@example.fr>',
        to='labo-info@listes.example.fr',
        subject='Re: [labo-info] Seminar next week',
        date=datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc),
        message_id='<abc@example.fr>',
        references='<parent@example.fr>',
        html='<p>Dear students,</p><p>Happy Speed Dating!</p>',
        text='Dear students,\n\nHappy Speed Dating!',
    )
    defaults.update(kw)
    return Mail(**defaults)


def roundtrip(msg):
    return BytesParser(policy=policy.default).parsebytes(bytes(msg))


def test_headers(cfg):
    msg = roundtrip(build_message(sample(), cfg))
    assert msg['Subject'] == '[Partage] Re: [labo-info] Seminar next week'
    assert msg['From'].addresses[0].display_name == 'Marie Dupont via Partage'
    assert msg['From'].addresses[0].addr_spec == 'bot@gmail.com'
    assert msg['To'] == 'me@gmail.com'
    assert msg['Reply-To'].addresses[0].addr_spec == 'marie.dupont@example.fr'
    assert msg['Message-ID'] == '<abc@example.fr>'
    assert msg['References'] == '<parent@example.fr>'


def test_body_is_not_lost(cfg):
    """The bug that started the rewrite: a forward with a banner and no body."""
    msg = roundtrip(build_message(sample(), cfg))
    html = msg.get_body(('html',)).get_content()
    text = msg.get_body(('plain',)).get_content()
    assert 'Happy Speed Dating!' in html and 'Happy Speed Dating!' in text
    assert 'marie.dupont@example.fr' in html
    assert 'Forwarded from Partage' in text


def test_list_reply_to_wins_and_non_ascii_names(cfg):
    mail = sample(sender='Élodie Müller <e@example.fr>', reply_to='liste@example.fr')
    msg = roundtrip(build_message(mail, cfg))
    assert msg['From'].addresses[0].display_name == 'Élodie Müller via Partage'
    assert msg['Reply-To'].addresses[0].addr_spec == 'liste@example.fr'


def test_attachments_and_inline_images(cfg):
    mail = sample(
        html='<img src="cid:logo@x">',
        inline=[Part('image/png', b'PNG', cid='logo@x')],
        attachments=[Part('application/pdf', b'%PDF', filename='planning.pdf'),
                     Part('message/rfc822', b'Subject: x\r\n\r\nhi', filename='x.eml')],
    )
    msg = roundtrip(build_message(mail, cfg))
    assert [a.get_filename() for a in msg.iter_attachments()] == ['planning.pdf', 'x.eml']
    related = [p for p in msg.walk() if p.get('Content-ID') == '<logo@x>']
    assert related and related[0].get_content() == b'PNG'


def test_oversized_attachments_are_dropped_and_listed(cfg, monkeypatch):
    monkeypatch.setattr(mailer, 'MAX_ENCODED_BYTES', 10_000)
    mail = sample(attachments=[Part('application/pdf', b'x' * 2000, filename='small.pdf'),
                               Part('application/zip', b'x' * 50_000, filename='huge.zip')])
    msg = roundtrip(build_message(mail, cfg))
    assert [a.get_filename() for a in msg.iter_attachments()] == ['small.pdf']
    assert 'huge.zip' in msg.get_body(('html',)).get_content()
    assert 'Too large' in msg.get_body(('plain',)).get_content()


def test_no_prefix(cfg):
    from dataclasses import replace
    msg = build_message(sample(), replace(cfg, subject_prefix=''))
    assert msg['Subject'] == 'Re: [labo-info] Seminar next week'


def test_html_to_text():
    assert html_to_text('<style>x{}</style><p>a&nbsp;b</p><p>c<br>d</p>') == 'a b\n\nc\nd'
