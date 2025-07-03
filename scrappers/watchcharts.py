import re
import json
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from concurrent.futures import ThreadPoolExecutor
import asyncio

from models.product_item import ProductModel
from models.bags import BagsModel
from models.shoes import ShoesModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel


class WatchChartsScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session(profile_name="auricore-dev-kunal")
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

        self.executor = ThreadPoolExecutor(max_workers=10)

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching WatchCharts for: {query}")
        url = f"https://watchcharts.com/listings?q={quote(query)}"
        results = []
        detail_tasks = []

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=False,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                retry=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                }
            ))

            soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")

            for anchor in soup.select("a.card-link[href^='/listing/']"):
                try:
                    link = f"https://watchcharts.com{anchor['href']}"
                    title_el = anchor.select_one(".card-title.card-title-watch")
                    price_el = anchor.select_one("h4")
                    image_el = anchor.select_one("img")
                    meta_el = anchor.select_one("div.font-sm")

                    title = title_el.get_text(strip=True) if title_el else "Unknown"
                    price = re.sub(r"[^\d.]", "", price_el.get_text(strip=True)) if price_el else "0"
                    image_url = image_el["src"] if image_el and image_el.has_attr("src") else None
                    meta = meta_el.get_text(strip=True) if meta_el else ""

                    condition = "Used" if "used" in title.lower() else "New" if "new" in title.lower() else "Unknown"
                    seller_platform = (
                        "Chrono24" if "chrono24" in meta.lower() else
                        "WatchUSeek" if "watchuseek" in meta.lower() else
                        "Reddit" if "reddit" in meta.lower() else
                        "Forum" if "forum" in meta.lower() else
                        "Unknown"
                    )

                    product = {
                        "title": title,
                        "listing_price": price,
                        "product_id": product_id,
                        "category": category,
                        "url": link,
                        "image_url": image_url,
                        "condition": condition,
                        "seller": seller_platform,
                        "shipping": "Varies",
                        "location": "Unknown",
                        "top_rated": False,
                        "source": "watchcharts"
                    }

                    detail_tasks.append(self.fetch_and_insert(product, category))
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping WatchCharts item due to parse error: {e}")

            await asyncio.gather(*detail_tasks)

            self.logger.info(f"✅ WatchCharts results fetched: {len(detail_tasks)}")
            return detail_tasks

        except Exception as e:
            self.logger.exception(f"❌ WatchCharts scraping failed: {e}")
            return []

    async def fetch_and_insert(self, base_data, category):
        details = await self.fetch_item_details(base_data["url"])
        base_data["item_details"] = details
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, self.insert_or_update_product, base_data, category)

    async def fetch_item_details(self, url):
        self.logger.info(f"🔍 Fetching item details from: {url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector=".listing-info",
                rendering_wait=4000,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                timeout=120000,
                retry=False
            ))

            soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
            item_details = {}

            # Description
            description_el = soup.select_one(".listing-description-text")
            item_details = {}

            if description_el:
                # Replace <br> with newlines and extract text
                raw_html = str(description_el)
                for br in description_el.find_all("br"):
                    br.replace_with("\n")
                full_text = description_el.get_text(separator="\n", strip=True)

                # Add entire description
                item_details["description"] = full_text

                # Extract item specifications as key-value pairs
                item_specifications = {}
                for line in full_text.split("\n"):
                    if ":" in line:
                        parts = line.split(":", 1)
                        key = parts[0].strip().lower().replace(" ", "_")
                        value = parts[1].strip()
                        item_specifications[key] = value

                item_details["item_specifications"] = item_specifications

            # Key-value table
            for row in soup.select(".table.table-bordered tr"):
                cols = row.find_all("td")
                if len(cols) == 2:
                    key = cols[0].get_text(strip=True).lower().replace(" ", "_").replace(":", "")
                    value = cols[1].get_text(strip=True)
                    item_details[key] = value

            return item_details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch item details from {url}: {e}")
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
        specs_data = details.get("item_specifications", {})  # Extract from parsed description text

        specs = {
            "Brand": details.get("brand") or details.get("designer") or specs_data.get("brand"),
            "Model": specs_data.get("model"),
            "Condition": base_data.get("condition", "Unknown"),
            "Size": details.get("size") or specs_data.get("size"),
            "Color": details.get("color") or specs_data.get("color"),
            "Box/Papers": details.get("material") or specs_data.get("box/papers"),
            "Movement": specs_data.get("movement"),
            "Water Resistance": specs_data.get("water_resistance"),
            "Case Material": specs_data.get("case_material"),
            "Luminous": specs_data.get("luminous"),
            "Year": details.get("year", "Unknown") or specs_data.get("year"),
            "Dust Bag": details.get("dust bag") or specs_data.get("dust_bag") or details.get("description"),
        }

        # Save into the corresponding category table
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
