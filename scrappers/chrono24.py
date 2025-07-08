import json
import asyncio
import uuid
from urllib.parse import quote, urlparse, parse_qs
from bs4 import BeautifulSoup
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from boto3.session import Session
from boto3.dynamodb.conditions import Key
import backoff

from price_parser import Price
from utils.logger import get_logger
from utils.utility import Utility
from utils.antropic import AnthropicRefiner
from models.product_item import ProductModel
from config.scrapper_config import Config

class Chrono24Scraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.anthropic_refiner = AnthropicRefiner()
        self.semaphore = asyncio.Semaphore(10)

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")

    @backoff.on_exception(
        backoff.expo,
        ScrapflyScrapeError,
        max_tries=4,
        giveup=lambda e: "OPERATION_TIMEOUT" not in str(e)
    )
    async def scrape_with_retries(self, config):
        self.logger.debug(f"\u23f3 Scraping URL: {config.url}")
        async with self.semaphore:
            return await self.client.async_scrape(config)

    async def search(self, query, category, product_id=None, image_query=None, search_query=None):
        base_url = (
            "https://www.chrono24.com/search/index.htm?"
            "catalogPageSize=60&sortorder=0&currencyId=USD&dosearch=true"
            "&query={query}&{filters}"
        )

        condition_filters = ["usedOrNew=used", "usedOrNew=new"]
        case_diameters = "&".join(f"caseDiameter={d}" for d in range(1, 38))

        urls = [
            base_url.format(query=quote(query), filters=f"{condition}&{case_diameters}")
            for condition in condition_filters
        ]

        self.logger.info(f"\ud83d\udd0d Launching Chrono24 search for: {query} with {len(urls)} conditions")
        tasks = [
            self.fetch_and_parse(url, category, product_id, image_query, search_query)
            for url in urls
        ]

        all_results = await asyncio.gather(*tasks)
        seen = set()
        merged = []
        for result_list in all_results:
            for item in result_list:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    merged.append(item)

        self.logger.info(f"\u2705 Total unique Chrono24 items inserted: {len(merged)}")
        return merged

    async def fetch_and_parse(self, url, category, product_id, image_query, search_query):
        try:
            self.logger.debug(f"\ud83c\udf10 Scraping filtered URL: {url}")
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
            ))
        except Exception as e:
            self.logger.exception(f"\u274c Failed to scrape filtered URL: {e}")
            return []

        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        condition = query_params.get("usedOrNew", ["Unknown"])[0]
        diameters = query_params.get("caseDiameter", [])
        size = f"{min(map(int, diameters))}mm - {max(map(int, diameters))}mm" if diameters else "Unknown"

        soup = BeautifulSoup(resp.scrape_result.get("content", ""), "html.parser")
        results = []

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "@graph" in data:
                    for node in data["@graph"]:
                        if node.get("@type") == "AggregateOffer" and "offers" in node:
                            for offer in node["offers"]:
                                item_url = offer.get("url", "").strip()
                                if not item_url:
                                    continue

                                title = offer.get("name", "").strip()
                                price_text = str(offer.get("price", "")).strip()
                                parsed_price = Price.fromstring(price_text)
                                price = parsed_price.amount_float

                                image_url = (
                                    offer.get("image", {}).get("contentUrl")
                                    if isinstance(offer.get("image"), dict)
                                    else offer.get("image")
                                )

                                base_data = {
                                    "product_id": product_id or str(uuid.uuid4()),
                                    "title": title,
                                    "listing_price": price,
                                    "url": item_url,
                                    "image_url": image_url,
                                    "condition": condition.capitalize(),
                                    "seller": offer.get("seller", {}).get("name", "Unknown"),
                                    "shipping": "Varies",
                                    "location": "Unknown",
                                    "top_rated": False,
                                    "source": "chrono24",
                                    "category": category,
                                    "image_query": image_query,
                                    "search_query": search_query,
                                    "size": size
                                }

                                results.append(base_data)
                                self.insert_or_update_product(base_data, category)

            except json.JSONDecodeError:
                self.logger.warning("\u26a0\ufe0f Skipped invalid JSON-LD script block")

        self.logger.info(f"\ud83d\udce6 Parsed {len(results)} products from {url}")
        return results

    def insert_or_update_product(self, base_data: dict, category: str):
        try:
            existing = self.product_table.get_item(Key={"url": base_data["url"]})
            product = ProductModel(base_data, category)

            if "Item" in existing:
                self.logger.info(f"\ud83d\udd04 Updating existing product: {base_data['url']}")
            else:
                self.logger.info(f"\u2795 Inserting new product: {base_data['url']}")

            self.product_table.put_item(Item=product.to_dict())
        except Exception as e:
            self.logger.exception(f"\u274c DynamoDB insert/update failed: {e}")