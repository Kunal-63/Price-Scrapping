class Config:
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
    SCRAPFLY_KEY = os.getenv("SCRAPFLY_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_IMAGE_URL = "https://images.ray-ban.com/is/image/RayBan/805289126577__STD__shad__fr.png"
    AWS_ACCESS_KEY = ""
    AWS_SECRET_KEY = ""
    AWS_REGION = "us-east-2"