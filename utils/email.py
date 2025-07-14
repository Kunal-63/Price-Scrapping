# utils/email_utils.py

import smtplib
from email.message import EmailMessage
from config.email import EmailConfig

def send_email(subject: str, body: str):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EmailConfig.EMAIL_USER
        msg["To"] = EmailConfig.EMAIL_TO
        msg.set_content(body)

        with smtplib.SMTP(EmailConfig.EMAIL_HOST, EmailConfig.EMAIL_PORT) as server:
            server.starttls()
            server.login(EmailConfig.EMAIL_USER, EmailConfig.EMAIL_PASS)
            server.send_message(msg)

        print("📧 Email sent successfully.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
