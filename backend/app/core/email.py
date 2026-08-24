"""
Minimal SMTP email sending. Uses Python's built-in smtplib/email
modules rather than a third-party library - for "send one plain-text
email" there's nothing a library would meaningfully simplify, and it's
one less dependency handling real mail credentials.
"""

import smtplib
from email.message import EmailMessage

from app.core.config import settings


def send_email(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()  # upgrade to an encrypted connection before sending the login credentials
        server.login(settings.smtp_username, settings.smtp_app_password)
        server.send_message(message)


def send_verification_email(to: str, token: str) -> None:
    verify_link = f"{settings.app_base_url}/auth/verify?token={token}"
    body = (
        "Welcome to Garmin AI!\n\n"
        "Please verify your email by opening this link:\n"
        f"{verify_link}\n\n"
        "This link expires in 24 hours."
    )
    send_email(to=to, subject="Verify your Garmin AI account", body=body)
