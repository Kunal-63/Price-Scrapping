import json
import re
import uuid
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from utils.logger import get_logger
from bs4 import BeautifulSoup
from utils.utility import Utility
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from config.scrapper_config import Config
from models.beauty import BeautyModel
from models.jewelry import JewelryModel

class GoogleShoppingScraper:
    def __init__(self, client):
        self.client = client
        self.logger = get_logger()

        session = Session(profile_name="auricore-dev-kunal")
        dynamodb = session.resource("dynamodb", region_name="us-east-1")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching Google Shopping for query: {query}")
        url = f"https://www.google.com/search?q={quote(query)}&udm=28"
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector='div.sh-dlr__list-result',
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                timeout=80000,
                retry=False,
                js="""
                    setTimeout(() => {
                        window.scrollTo(0, document.body.scrollHeight);
                    }, 3000);
                """,
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Google Shopping results: {e}")
            return []

        results = []
        try:
            xhr_calls = resp.scrape_result["browser_data"].get("xhr_call", [])
            api_call = next((c for c in xhr_calls if "googleapis" in c["url"] and "shop" in c["url"]), None)

            if api_call:
                data = json.loads(api_call["response"]["body"])
                items = data.get("items", [])
                for item in items:
                    try:
                        title = item.get("title")
                        price = item.get("price", {}).get("value")
                        currency = item.get("price", {}).get("currency")
                        link = item.get("link")
                        image_url = item.get("image")
                        seller = item.get("merchant", {}).get("name", "Unknown Seller")

                        if not (title and price and link):
                            continue

                        base_data = {
                            "title": title,
                            "product_id": product_id,
                            "listing_price": str(price),
                            "currency": currency or "USD",
                            "url": link,
                            "image_url": image_url,
                            "condition": "New",
                            "seller": seller,
                            "shipping": "Varies",
                            "location": "Unknown",
                            "top_rated": False,
                            "source": "google_shopping",
                            "category": category,
                            "created_at": Utility.current_utc_time(),
                            "item_details": {}
                        }

                        self.insert_or_update_product(base_data, category)
                        results.append(base_data)
                    except Exception as e:
                        self.logger.warning(f"⚠️ Skipping XHR item due to parse error: {e}")
                if results:
                    self.logger.info(f"✅ Google Shopping XHR results fetched: {len(results)}")
                    return results
        except Exception as e:
            self.logger.warning(f"⚠️ XHR parse failed, falling back to HTML scraping: {e}")

        # Fallback: HTML scraping
        try:
            html = resp.scrape_result.get("content", "")
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select("div.sh-dlr__list-result")

            for card in cards:
                try:
                    title = card.select_one("h4 > span") or card.select_one("h4")
                    price = card.select_one("span.a8Pemb")
                    link_tag = card.select_one("a.shntl")
                    image = card.select_one("img")
                    seller = card.select_one("div.aULzUe")

                    if not (title and price and link_tag):
                        continue

                    base_data = {
                        "title": title.text.strip(),
                        "listing_price": price.text.strip().replace("$", ""),
                        "currency": "USD",
                        "product_id": product_id,
                        "url": "https://www.google.com" + link_tag["href"],
                        "image_url": image["src"] if image and image.has_attr("src") else None,
                        "condition": "New",
                        "seller": seller.text.strip() if seller else "Unknown",
                        "shipping": "Varies",
                        "location": "Unknown",
                        "top_rated": False,
                        "source": "google_shopping",
                    
                        "category": category,
                        "created_at": Utility.current_utc_time(),
                        "item_details": {}
                    }

                    self.insert_or_update_product(base_data, category)
                    results.append(base_data)
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping HTML item due to parse error: {e}")
        except Exception as e:
            self.logger.exception("❌ Error during HTML fallback parsing")

        self.logger.info(f"✅ Google Shopping final results fetched: {len(results)}")
        return results

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

        specs = {
            "Brand": base_data.get("title", "").split(" ")[0],
            "Condition": base_data.get("condition", "Unknown"),
        }

        if category == "Shoes":
            meta = ShoesModel(product.product_id, specs, base_data["url"])
            self.shoes_table.put_item(Item=meta.to_dict())
        elif category == "Bags":
            meta = BagsModel(product.product_id, specs, base_data["url"])
            self.bags_table.put_item(Item=meta.to_dict())
        elif category == "Accessories":
            meta = AccessoriesModel(product.product_id, specs, base_data["url"])
            self.accessories_table.put_item(Item=meta.to_dict())
        elif category == "Clothing":
            meta = ClothingModel(product.product_id, specs, base_data["url"])
            self.clothing_table.put_item(Item=meta.to_dict())
        else:
            self.logger.warning(f"❌ Unknown category: {category}, skipping metadata.")
