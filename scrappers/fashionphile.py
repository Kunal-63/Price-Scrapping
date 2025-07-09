import re
import uuid
import json
import asyncio
from urllib.parse import quote, urljoin
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
from utils.logger import get_logger
from utils.utility import Utility
from models.product_item import ProductModel
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
import datetime
from config.scrapper_config import Config
import backoff
from utils.antropic import AnthropicRefiner
from price_parser import Price
from concurrent.futures import ThreadPoolExecutor

class FashionphileScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")
        self.beauty_table = dynamodb.Table("BeautyTable")
        self.jewelry_table = dynamodb.Table("JewelryTable")

        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()
        self.executor = ThreadPoolExecutor(max_workers=8)

        self.condition_map = {
            "giftable": "Giftable",
            "new": "New",
            "excellent": "Excellent",
            "shows-wear": "Shows Wear",
            "worn": "Worn",
            "fair": "Fair",
            "flawed": "Flawed"
        }

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=4,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)
        
    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching Vestiaire for: {query} across all conditions")

        tasks = [
            self.search_by_condition(query, category, product_id, image_query, search_query, code, label)
            for code, label in self.condition_map.items()
        ]

        all_results = await asyncio.gather(*tasks)
        flat_results = [item for sublist in all_results for item in sublist]
        return flat_results

    async def search_by_condition(self, query, category, product_id, image_query, search_query, condition_code, condition_label):
        self.logger.info(f"🔍 Searching Fashionphile for query: {query}")
        url = f"https://www.fashionphile.com/shop?search={quote(query)}&condition={condition_code}"
        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="li.ais-Hits-item",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Fashionphile listings: {e}")
            return []

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("li", class_="ais-Hits-item")

        results = []

        for card in items:
            try:
                link_tag = card.find("a", class_="linkHover")
                title_tag = card.find("a", class_="hitTitle")
                brand_tag = card.find("span", class_="brand")
                price_tag = card.find("span", itemprop="price")
                image_tag = link_tag.find("img") if link_tag else None

                if not (link_tag and title_tag and brand_tag and price_tag):
                    continue

                title = f"{brand_tag.text.strip()} {title_tag.text.strip()}"
                price_text = price_tag.text.strip()
                price_data = Price.fromstring(price_text)
                price = price_data.amount_float if price_data.amount_float else None
                url_full = urljoin("https://www.fashionphile.com", link_tag["href"])
                image_url = image_tag["src"] if image_tag and image_tag.has_attr("src") else None

                if(condition_label == "Giftable"):
                    condition_label = "Like New"
                elif(condition_label == "Shows Wear"):
                    condition_label = "Good"
                elif(condition_label == "Worn" or condition_label == "Flawed"):
                    condition_label = "Fair"


                size = self.anthropic_refiner.extract_size_from_title(title=title)
                size = Utility.refine_us_size_string(size)

                location = Utility.extract_location_from_title(title=title)

                base_data = {
                    "title": title,
                    "listing_price": price,
                    "url": url_full,
                    "image_url": image_url,
                    "condition": condition_label,
                    "seller": "Fashionphile Verified",
                    "shipping": "Free Shipping in US",
                    "location": location,
                    "top_rated": True,
                    "source": "fashionphile",
                    "image_query": image_query,
                    "search_query": search_query,
                    "product_id": product_id,
                    "category": category,
                    "size": size
                }

                product = ProductModel(base_data, category)
                results.append(product.to_dict())

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to parse a product card: {e}")
                continue

        self.bulk_insert_products(results)

        self.logger.info(f"✅ Fashionphile results fetched: {len(results)}")
        return results

    def bulk_insert_products(self, items: list):
        if not items:
            return

        def write_batch(batch):
            try:
                with self.product_table.batch_writer(overwrite_by_pkeys=["url"]) as writer:
                    for item in batch:
                        writer.put_item(Item=item)
                self.logger.info(f"✅ Inserted batch of {len(batch)} items.")
            except Exception as e:
                self.logger.exception(f"❌ Batch insert failed: {e}")

        # Split into chunks of 25
        def chunks(data, size=25):
            for i in range(0, len(data), size):
                yield data[i:i + size]

        batches = list(chunks(items, 25))
        futures = [self.executor.submit(write_batch, batch) for batch in batches]

        # Optional: wait for all threads to finish
        for f in futures:
            f.result()