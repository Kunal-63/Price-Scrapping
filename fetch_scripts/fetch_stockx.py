import asyncio
import time
from datetime import datetime, timedelta
import boto3
from boto3.session import Session

from utils.logger import get_logger
from scrappers.stockx import StockXScraper
from config.scrapper_config import Config
from config.scrapping_tracker_config import ScrappingTrakcerConfig

class StockXFetcher:
    def __init__(self, concurrency: int = 5):
        self.logger = get_logger(name="stockx_fetch")
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(self.concurrency)

        self.session = Session()
        self.dynamodb = self.session.resource("dynamodb", region_name="us-east-1")
        self.table = self.dynamodb.Table("FinalProductsTable")
        self.tracker = self.dynamodb.Table("ScraperRunTracker")
        self.scraper = StockXScraper(Config.SCRAPFLY_KEY)

    def fetch_all_items(self):
        """Paginate through DynamoDB scan and return all items."""
        all_items = []
        response = self.table.scan()
        all_items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            response = self.table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            all_items.extend(response.get("Items", []))

        self.logger.info(f"📦 Total items fetched from DynamoDB: {len(all_items)}")
        return all_items[:50]

    def update_run_tracker(self, product_id, source="stockx"):
        now = datetime.utcnow().isoformat()
        next_run = (datetime.utcnow() + timedelta(hours=ScrappingTrakcerConfig.STOCKX)).isoformat()

        self.tracker.put_item(Item={
            "product_id": product_id,
            "source": source,
            "last_run": now,
            "next_run": next_run
        })

    async def process_item(self, item):
        async with self.semaphore:
            start = time.time()
            try:
                query = item.get("canonical_name")
                category = item.get("category")
                product_id = item.get("product_id")
                imageurls = item.get("all_image_urls", [])
                image = imageurls[0] if imageurls else None

                self.logger.info(f"🔍 Starting StockX search for: {query}")

                result = await self.scraper.search(
                    query=query,
                    category=category,
                    product_id=product_id,
                    image_query=query,
                    search_query=query
                )

                self.logger.info(f"✅ {query}: {len(result)} length")
                self.logger.info(f"⏱️ Took {time.time() - start:.2f}s")

                self.update_run_tracker(product_id)
                return result
            except Exception as e:
                self.logger.error(f"❌ Error for {item.get('canonical_name')}: {e}")
                return None

    async def run(self, products: list = None):
        items = products if products else self.fetch_all_items()
        self.logger.info(f"🔧 Beginning processing of {len(items)} item(s)...")

        start_all = time.time()
        tasks = [asyncio.create_task(self.process_item(item)) for item in items]
        await asyncio.gather(*tasks)

        total_time = time.time() - start_all
        self.logger.info(f"✅ All items processed in {total_time:.2f} seconds")
