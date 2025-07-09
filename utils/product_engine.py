import asyncio
import json
import os
import re
from urllib.parse import quote

from scrapfly import ScrapflyClient, ScrapeConfig

from utils.image_search import ImageSearcher
from utils.antropic import AnthropicRefiner
from config.scrapper_config import Config
from scrappers.ebay import EbayScraper
from scrappers.serp import SerpAPISearcher
from scrappers.stockx import StockXScraper
from scrappers.goat import GoatScraper
from scrappers.flightclub import FlightClubScraper
from scrappers.watchcharts import WatchChartsScraper
from scrappers.chrono24 import Chrono24Scraper
from scrappers.fashionphile import FashionphileScraper
from scrappers.realreal import RealRealScraper
from scrappers.rebag import RebagScraper
from scrappers.vestiaire import VestiaireScraper
from scrappers.grailed import GrailedScraper
from scrappers.farfetch import FarfetchScraper
from scrappers.google_shopping import GoogleShoppingScraper
from utils.logger import get_logger

logger = get_logger("search_engine")

class ProductSearchEngine:
    def __init__(self):
        client = ScrapflyClient(key=Config.SCRAPFLY_KEY)

        self.image_searcher = ImageSearcher()
        self.query_refiner = AnthropicRefiner()
        self.ebay_scraper = EbayScraper(Config.SCRAPFLY_KEY)
        self.serpapi_searcher = SerpAPISearcher()
        self.stockx_scraper = StockXScraper(Config.SCRAPFLY_KEY)
        self.goat_scraper = GoatScraper(Config.SCRAPFLY_KEY)
        self.flightclub = FlightClubScraper(Config.SCRAPFLY_KEY)
        self.watchcharts = WatchChartsScraper(Config.SCRAPFLY_KEY)
        self.chrono24 = Chrono24Scraper(Config.SCRAPFLY_KEY)
        self.fashionphile = FashionphileScraper(Config.SCRAPFLY_KEY)
        self.realreal = RealRealScraper(Config.SCRAPFLY_KEY)
        self.rebag = RebagScraper(Config.SCRAPFLY_KEY)
        self.vestiaire = VestiaireScraper(Config.SCRAPFLY_KEY)
        self.grailed = GrailedScraper(Config.SCRAPFLY_KEY)
        self.farfetch = FarfetchScraper(Config.SCRAPFLY_KEY)
        self.google_shopping = GoogleShoppingScraper(client)

    async def search(self, query: str = None, image_url: str = None, category: str = None, product_id: str = None):
        final_query = None
        image_query = None
        combined_query = None

        # if image_url:
        #     if not image_url.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        #         logger.error("Unsupported image format.")
        #         return

        #     logger.info("Extracting product name from image...")
        #     base_name = self.image_searcher.reverse_image_search(image_url)
        #     if not base_name:
        #         logger.error("Unable to determine product name from image.")
        #         return

        #     logger.info("Refining query using Gemini (from image)...")
        #     final_query = self.query_refiner.refine_query(base_name, image_url)
        #     image_query = final_query

        # elif query:
        #     logger.info("Refining query using Gemini (from text query)...")
        #     final_query = query.strip()

        # if not final_query:
        #     logger.error("No valid query generated.")
        #     return

        logger.info(f"Final Search Query: {final_query}")

        async def fetch(scraper, name):
            try:
                result = await scraper.search(query, category, product_id, image_query, final_query)
                if isinstance(result, dict) and "active" in result:
                    logger.info(f"{name} Results: {len(result['active'])} active, {len(result['sold'])} sold")
                else:
                    logger.info(f"{name} Results: {len(result)} items found")
                return result
            except Exception as e:
                logger.error(f"Error fetching from {name}: {e}")
                return {"active": [], "sold": []} if name == "eBay" else []

        tasks = [
            ("ebay_results", fetch(self.ebay_scraper, "eBay")),
            ("stockx_results", fetch(self.stockx_scraper, "StockX")),
            # ("goat_results", fetch(self.goat_scraper, "Goat")),
            # ("flightclub_results", fetch(self.flightclub, "Flight Club")),
            ("fashionphile_results", fetch(self.fashionphile, "Fashionphile")),
            ("realreal_results", fetch(self.realreal, "The RealReal")),
            ("rebag_results", fetch(self.rebag, "Rebag")),
            ("vestiaire_results", fetch(self.vestiaire, "Vestiaire")),
            ("grailed_results", fetch(self.grailed, "Grailed")),
            ("farfetch_results", fetch(self.farfetch, "Farfetch")),
            ("google_shopping_results", fetch(self.serpapi_searcher, "Google Shopping")),
        ]

        if category == 'accessories':
            tasks.extend([
                ("watchcharts_results", fetch(self.watchcharts, "WatchCharts")),
                ("chrono24_results", fetch(self.chrono24, "Chrono24")),
            ])

        names, coroutines = zip(*tasks)

        results = await asyncio.gather(*coroutines)

        combined = {
            "query": final_query,
            **dict(zip(names, results))
        }


        logger.info("Final Combined Results prepared.")
        return combined
