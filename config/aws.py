import os

class AwsConfig:
    AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    AWS_USERNAME = os.getenv("AWS_USERNAME", "sc2")
    PRODUCT_TABLE = os.getenv("PRODUCT_TABLE", "ProductsTable")
    SCRAPER_TRACKING_TABLE = os.getenv("SCRAPER_TRACKING_TABLE", "ScraperRunTracker")
    FINAL_PRODUCT_TABLE = os.getenv("FINAL_PRODUCT_TABLE", "FinalProductsTable")
    ARCHIVE_PRODUCTS_TABLE = os.getenv("ARCHIVE_PRODUCTS_TABLE", "ArchiveProductsTable")
    FETCHERS_LOCK_TABLE = os.getenv("FETCHERS_LOCK_TABLE", "FetcherLocks")
    SCRAPER_STATS_TABLE = os.getenv("SCRAPER_STATS_TABLE", "ScraperStats")
