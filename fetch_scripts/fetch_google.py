import asyncio
import time
from datetime import datetime, timedelta
import uuid
import boto3
from boto3.session import Session

from config.aws import AwsConfig
from utils.logger import get_logger
from scrappers.serp import SerpAPISearcher
from config.scrapper_config import Config
from config.scrapping_tracker_config import ScrappingTrakcerConfig

class GoogleFetcher:
    def __init__(self, concurrency: int = 20):
        self.logger = get_logger(name="serp_fetch")
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(self.concurrency)

        self.session = Session()
        self.dynamodb = self.session.resource("dynamodb", region_name=AwsConfig.AWS_REGION)
        self.table = self.dynamodb.Table(AwsConfig.FINAL_PRODUCT_TABLE)
        self.tracker = self.dynamodb.Table(AwsConfig.SCRAPER_TRACKING_TABLE)
        self.stats_table = self.dynamodb.Table(AwsConfig.SCRAPER_STATS_TABLE)

        self.scraper = SerpAPISearcher()

    def fetch_all_items(self):
        """Paginate through DynamoDB scan and return all items."""
        all_items = []
        response = self.table.scan()
        all_items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            response = self.table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            all_items.extend(response.get("Items", []))

        self.logger.info(f"📦 Total items fetched from DynamoDB: {len(all_items)}")
        return all_items[:10]

    def update_run_tracker(self, product_id, source="google"):
        now = datetime.utcnow().isoformat()
        next_run = (datetime.utcnow() + timedelta(hours=ScrappingTrakcerConfig.GOOGLE)).isoformat()
        self.tracker.put_item(Item={
            "product_id": product_id,
            "source": source,
            "last_run": now,
            "next_run": next_run
        })

    def update_stats(self, source: str, total_results: int, products_ran: int, time_taken: float):
        now = datetime.utcnow().isoformat()
        run_id = f"{now}_{uuid.uuid4().hex[:6]}"

        try:
            self.stats_table.put_item(
                Item={
                    "source": source,
                    "run_id": run_id,
                    "total_results": total_results,
                    "products_ran": products_ran,
                    "time_taken": round(time_taken, 2),
                    "timestamp": now
                }
            )
            self.logger.info(f"📊 Stats recorded: {total_results} results, {products_ran} products, {time_taken:.2f}s")
        except Exception as e:
            self.logger.error(f"❌ Failed to write ScraperStats: {e}")

    async def process_item(self, item):
        async with self.semaphore:
            start = time.time()
            try:
                query = item.get("canonical_name")
                category = item.get("category")
                product_id = item.get("product_id")
                imageurls = item.get("all_image_urls", [])
                image = imageurls[0] if imageurls else None

                self.logger.info(f"🔍 Starting SERP search for: {query}")

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
        results = await asyncio.gather(*tasks)

        total_time = time.time() - start_all
        total_results = sum(len(r) for r in results)
        products_ran = len(items)

        self.update_stats("google", total_results, products_ran, total_time)
        self.logger.info(f"✅ All items processed in {total_time:.2f} seconds")