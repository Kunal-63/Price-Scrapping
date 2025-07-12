import re
import json
import asyncio
import httpx
from config.scrapper_config import Config
from utils.logger import get_logger
from utils.utility import Utility
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from price_parser.parser import Price
import backoff
from utils.antropic import AnthropicRefiner
from utils.database import is_duplicate_today

class SerpAPISearcher:
    def __init__(self):
        self.logger = get_logger()
        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.semaphore = asyncio.Semaphore(10)
        self.anthropic_refiner = AnthropicRefiner()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def fetch_with_retries(self, url, params=None, headers=None):
        async with self.semaphore:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(url, params=params, headers=headers)
                res.raise_for_status()
                return res

    async def search(self, query, category, product_id, image_query, search_query):
        self.logger.info(f"🔍 Searching SerpAPI for query: {query}")

        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": Config.SERPAPI_KEY,
            "hl": "en",
        }

        try:
            res = await self.fetch_with_retries("https://serpapi.com/search.json", params=params)
            products = res.json().get("shopping_results", [])
            results = []
            tasks = []

            for p in products:
                link = p.get("product_link")
                if is_duplicate_today(url=link):
                    continue

                title = p.get("title")
                if not Utility.combined_similarity(title=title, query=query):
                    continue
                
                raw_price = p.get("price")
                condition = p.get("condition") or "New"
                seller = p.get("source") or "Unknown"
                shipping = (
                    p.get("shipping", {}).get("price") if isinstance(p.get("shipping"), dict)
                    else "Not specified"
                )
                location = p.get("extracted_location", "Unknown")
                image_url = p.get("thumbnail")
                size = p.get("size", "Unknown")
                size = Utility.refine_us_size_string(size)
                brand = p.get("brand", "Unknown")

                if not (title and link and raw_price):
                    continue

                parsed_price = Price.fromstring(raw_price)
                price = parsed_price.amount_float if parsed_price and parsed_price.amount_float else None

                # Refine location
                location = Utility.extract_location_from_title(title=title)

                if('new' in condition.lower()):
                    condition = "New"
                elif('pre' in condition.lower()):
                    condition = "Fair"

                item = {
                    "title": title,
                    "listing_price": str(price) if price else None,
                    "url": link,
                    "condition": condition,
                    "seller": seller,
                    "shipping": shipping,
                    "location": location,
                    "image_url": image_url,
                    "top_rated": False,
                    "source": "google_shopping",
                    "image_query": image_query,
                    "search_query": search_query,
                    "category": category,
                    "product_id": product_id,
                    "size": size,
                    "brand": brand
                }

                results.append(item)
                tasks.append(asyncio.create_task(self.insert_or_update_product(item, category)))

            await asyncio.gather(*tasks)
            self.logger.info(f"[SerpAPI] ✅ Google Shopping results fetched and inserted: {len(results)} SERP")
            return results

        except Exception as e:
            self.logger.exception("[SerpAPI] ❌ SerpAPI Shopping request failed")
            return []

    async def insert_or_update_product(self, base_data: dict, category: str):
        try:
            product = ProductModel(base_data, category)
            await asyncio.to_thread(self.product_table.put_item, Item=product.to_dict())
        except Exception as e:
            self.logger.exception(f"[SerpAPI] ❌ Failed to insert product into DynamoDB: {e}")
