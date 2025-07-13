# utils/email_utils.py

import smtplib
from email.message import EmailMessage

EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USER = "kunaladwani.work@gmail.com"
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_TO = "kunaladwani.work@gmail.com"

def send_email(subject: str, body: str):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.set_content(body)

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)

        print("📧 Email sent successfully.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")