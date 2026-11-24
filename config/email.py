import os

class EmailConfig:
    EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
    EMAIL_USER = os.getenv("EMAIL_USER", "kunaladwani.work@gmail.com")
    EMAIL_PASS = os.getenv("EMAIL_PASS", "")
    EMAIL_TO = os.getenv("EMAIL_TO", "kunaladwani.work@gmail.com")
