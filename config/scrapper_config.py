import os

class Config:
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
    SCRAPFLY_KEY = os.getenv("SCRAPFLY_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_IMAGE_URL = os.getenv("DEFAULT_IMAGE_URL", "")
