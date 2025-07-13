import asyncio
import time
from datetime import datetime
import boto3
from boto3.session import Session

from fetch_scripts.fetch_ebay import EbayFetcher
from fetch_scripts.fetch_farfetch import FarFetchFetcher
from fetch_scripts.fetch_fashionphile import FashionphileFetcher
from fetch_scripts.fetch_google import GoogleFetcher
from fetch_scripts.fetch_rebag import RebagFetcher
from fetch_scripts.fetch_stockx import StockXFetcher
from utils.email import send_email

# Map source to (fetcher class, concurrency limit)
FETCHER_MAP = {
    "ebay": (EbayFetcher, 20),
    "google": (GoogleFetcher, 20),
    "farfetch": (FarFetchFetcher, 15),
    "fashionphile": (FashionphileFetcher, 15),
    "rebag": (RebagFetcher, 15),
    "stockx": (StockXFetcher, 5)
}

class Scheduler:
    def __init__(self):
        session = Session()
        self.dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.tracker_table = self.dynamodb.Table("ScraperRunTracker")
        self.product_table = self.dynamodb.Table("FinalProductsTable")

    def get_due_products(self):
        now = datetime.utcnow().isoformat()
        due_items = []

        response = self.tracker_table.scan()
        items = response.get("Items", [])

        while "LastEvaluatedKey" in response:
            response = self.tracker_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))

        for item in items:
            if item.get("next_run") and item["next_run"] <= now:
                due_items.append((item["product_id"], item["source"]))

        return due_items

    def get_product_details(self, product_id):
        response = self.product_table.get_item(Key={"product_id": product_id})
        return response.get("Item")

    async def run_source_group(self, source, product_ids, fetcher_cls, concurrency_limit):
        fetcher = fetcher_cls(concurrency=concurrency_limit)
        tasks = []

        for product_id in product_ids[:concurrency_limit]:
            product = self.get_product_details(product_id)
            if product:
                tasks.append(fetcher.run(single_product=product))
            else:
                print(f"⚠️ Product not found in DB: {product_id}")

        await asyncio.gather(*tasks)
        print(f"✅ Finished processing {len(tasks)} {source} products.")

    async def run_due_fetchers(self):
        due = self.get_due_products()
        if not due:
            print("⏳ No products due for scraping right now.")
            return

        print(f"🔁 {len(due)} product(s) due for fetch.")

        grouped = {}
        for product_id, source in due:
            if source not in FETCHER_MAP:
                print(f"⚠️ Unsupported source: {source}")
                continue
            grouped.setdefault(source, []).append(product_id)

        tasks = []
        for source, product_ids in grouped.items():
            fetcher_cls, max_concurrent = FETCHER_MAP[source]
            tasks.append(self.run_source_group(source, product_ids, fetcher_cls, max_concurrent))

        await asyncio.gather(*tasks)
        summary = f"✅ {sum(len(p) for p in grouped.values())} products fetched:\n"
        for source, ids in grouped.items():
            summary += f"- {source}: {len(ids)}\n"

        send_email("🗂️ Scraper Run Summary", summary)
        print("✅ All due fetchers completed.")

async def poll_every_hour():
    scheduler = Scheduler()

    while True:
        print(f"🕐 Running scheduler at {datetime.utcnow().isoformat()}...")
        await scheduler.run_due_fetchers()
        print("💤 Sleeping for 1 hour...\n")
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(poll_every_hour())
