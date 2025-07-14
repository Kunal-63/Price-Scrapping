class EmailConfig:
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USER = "kunaladwani.work@gmail.com"
    EMAIL_PASS = os.getenv("EMAIL_PASS", "")
    EMAIL_TO = "kunaladwani.work@gmail.com"