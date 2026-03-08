import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def forward(email: dict, cfg: dict):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"[Partage Fwd] {email['subject']}"
    msg['From'] = cfg['gmail_user']
    msg['To'] = cfg['forward_to']

    body_text = (
        f"--- Forwarded from Partage ---\n"
        f"From: {email['from']}\n"
        f"Date: {email['date']}\n"
        f"Subject: {email['subject']}\n\n"
    )
    msg.attach(MIMEText(body_text, 'plain'))
    msg.attach(MIMEText(email['body'], 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(cfg['gmail_user'], cfg['gmail_password'])
        server.sendmail(cfg['gmail_user'], cfg['forward_to'], msg.as_string())
