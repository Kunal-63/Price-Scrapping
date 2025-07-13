import asyncio
from datetime import datetime
from boto3.session import Session

from fetch_scripts.fetch_ebay import EbayFetcher
from fetch_scripts.fetch_farfetch import FarFetchFetcher
from fetch_scripts.fetch_fashionphile import FashionphileFetcher
from fetch_scripts.fetch_google import GoogleFetcher
from fetch_scripts.fetch_rebag import RebagFetcher
from fetch_scripts.fetch_stockx import StockXFetcher
from utils.email import send_email

# Map source to fetcher class only
FETCHER_MAP = {
    "ebay": EbayFetcher,
    "google": GoogleFetcher,
    "farfetch": FarFetchFetcher,
    "fashionphile": FashionphileFetcher,
    "rebag": RebagFetcher,
    "stockx": StockXFetcher
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

    async def run_source_group(self, source, products, fetcher_cls):
        print(f"⚙️ Running {source} fetcher for {len(products)} products")
        fetcher = fetcher_cls()
        await fetcher.run(products=products)

    async def run_due_fetchers(self):
        due = self.get_due_products()
        if not due:
            print("⏳ No products due for scraping right now.")
            return

        print(f"🔁 {len(due)} product(s) due for fetch.")

        product_ids = {product_id for product_id, _ in due}

        # Load all product metadata once
        product_data_map = {}
        for product_id in product_ids:
            item = self.get_product_details(product_id)
            if item:
                product_data_map[product_id] = item
            else:
                print(f"⚠️ Product not found: {product_id}")

        # Group by source
        grouped = {}
        for product_id, source in due:
            if source not in FETCHER_MAP:
                print(f"⚠️ Unsupported source: {source}")
                continue
            product = product_data_map.get(product_id)
            if product:
                grouped.setdefault(source, []).append(product)

        # Run all fetchers
        tasks = []
        for source, product_list in grouped.items():
            fetcher_cls = FETCHER_MAP[source]
            tasks.append(self.run_source_group(source, product_list, fetcher_cls))

        await asyncio.gather(*tasks)

        # Send summary
        summary = f"✅ {sum(len(p) for p in grouped.values())} products fetched:\n"
        for source, items in grouped.items():
            summary += f"- {source}: {len(items)}\n"

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
