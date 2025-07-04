import re
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from config.scrapper_config import Config
from utils.logger import get_logger
from utils.utility import Utility
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel


class SerpAPISearcher:
    def __init__(self):
        self.logger = get_logger()
        
        session = Session(profile_name=Config.AWS_USERNAME)
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")


    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching SerpAPI for query: {query}")

        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": Config.SERPAPI_KEY,
            "hl": "en",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get("https://serpapi.com/search.json", params=params)
                res.raise_for_status()
                products = res.json().get("shopping_results", [])

            results = []
            detail_tasks = []

            for p in products:
                title = p.get("title")
                price = p.get("price")
                link = p.get("product_link")
                condition = p.get("condition") or "Unknown"
                seller = p.get("source") or "Unknown"
                shipping = p.get("shipping", {}).get("price") if isinstance(p.get("shipping"), dict) else "Not specified"
                location = p.get("extracted_location") if "extracted_location" in p else "Unknown"
                image_url = p.get("thumbnail")

                if not (title and link and price):
                    continue

                results.append({
                    "title": title,
                    "listing_price": re.sub(r"[^\d.]", "", price),
                    "url": link,
                    "condition": condition,
                    "seller": seller,
                    "shipping": shipping,
                    "location": location,
                    "image_url": image_url,
                    "top_rated": False,
                    "source": "google_shopping"
                })
                detail_tasks.append(self.get_product_details(link))

            self.logger.info(f"🧵 Fetching product detail pages concurrently...")
            detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

            for i, detail in enumerate(detail_results):
                item = results[i]
                item["item_details"] = detail if isinstance(detail, dict) else {}
                item["product_id"] = product_id
                item["category"] = category
                self.insert_or_update_product(item, category)

            self.logger.info(f"✅ Google Shopping results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception("❌ SerpAPI Shopping request failed")
            return []

    async def get_product_details(self, url):
        self.logger.info(f"🔍 Fetching product details from Google Shopping URL: {url}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(url, headers=headers)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "html.parser")

            description = []
            details = {}

            for li in soup.select("ul, .product-details, .specs, .a-unordered-list")[:3]:
                for item in li.select("li"):
                    text = item.get_text(strip=True)
                    if text:
                        description.append(text)

            for row in soup.select("table tr"):
                cols = row.select("td, th")
                if len(cols) == 2:
                    key = cols[0].text.strip().lower().replace(" ", "_").replace(":", "")
                    val = cols[1].text.strip()
                    details[key] = val

            details["description"] = description
            return details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch/parse product details from: {url} | {e}")
            return {}

    def insert_or_update_product(self, base_data: dict, category: str):
        existing = self.product_table.scan(
            FilterExpression=Attr("url").eq(base_data["url"])
        )

        product = ProductModel(base_data, category)

        if existing["Items"]:
            self.logger.info(f"🔄 Updating existing product in DB: {base_data['url']}")
        else:
            self.logger.info(f"➕ Inserting new product in DB: {base_data['url']}")

        self.product_table.put_item(Item=product.to_dict())

        details = base_data.get("item_details", {})
        specs = {
            "Brand": base_data.get("seller"),
            "Condition": base_data.get("condition", "Unknown"),
            "Year": details.get("year", "Unknown"),
            "Size": details.get("size") or details.get("product_dimensions"),
            "Color": details.get("color"),
            "Shape": details.get("shape"),
            "Closure": details.get("closure"),
            "Box/Papers": None,
            "Dust Bag": details.get("description"),
        }

        if category == "Shoes":
            meta = ShoesModel(product.product_id, specs)
            self.shoes_table.put_item(Item=meta.to_dict())
        elif category == "Bags":
            meta = BagsModel(product.product_id, specs)
            self.bags_table.put_item(Item=meta.to_dict())
        elif category == "Accessories":
            meta = AccessoriesModel(product.product_id, specs)
            self.accessories_table.put_item(Item=meta.to_dict())
        elif category == "Clothing":
            meta = ClothingModel(product.product_id, specs)
            self.clothing_table.put_item(Item=meta.to_dict())
        else:
            self.logger.warning(f"❌ Unknown category: {category}, skipping metadata.")
