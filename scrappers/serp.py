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


class SerpAPISearcher:
    def __init__(self):
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

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

            for p in products:
                title = p.get("title")
                raw_price = p.get("price")
                link = p.get("product_link")
                condition = p.get("condition") or "Unknown"
                seller = p.get("source") or "Unknown"
                shipping = p.get("shipping", {}).get("price") if isinstance(p.get("shipping"), dict) else "Not specified"
                location = p.get("extracted_location") if "extracted_location" in p else "Unknown"
                image_url = p.get("thumbnail")
                size = p.get("size", "Unknown")
                brand = p.get('brand', "Unknown")
                parsed_price = Price.fromstring(raw_price).amount if raw_price else None

                if not (title and link and raw_price):
                    continue
                
                location = self.anthropic_refiner.extract_location_from_title(title=title)

                item = {
                    "title": title,
                    "listing_price": str(parsed_price),
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
                self.insert_or_update_product(item, category)

            self.logger.info(f"✅ Google Shopping results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception("❌ SerpAPI Shopping request failed")
            return []

    def insert_or_update_product(self, base_data: dict, category: str):
        product = ProductModel(base_data, category)
        self.logger.info(f"➕ Inserting new product in DB: {base_data['url']}")

        self.product_table.put_item(Item=product.to_dict())
