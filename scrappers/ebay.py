from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
import re
from urllib.parse import quote
from utils.logger import get_logger
from utils.utility import Utility
import asyncio
import backoff
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
from boto3.session import Session
import botocore
import numpy as np
from config.scrapper_config import Config
from boto3.dynamodb.conditions import Key, Attr

class EbayScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.MAX_CONCURRENT_DETAIL_FETCHES = 5

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

    async def search(self, query, category, product_id):
        if(not category):
            raise ValueError("Category must be specified for eBay search.")
            
        self.logger.info(f"🔍 Starting eBay search for: {query}")
        try:
            active = await self.get_active_listings(query, category, product_id)
            sold = await self.get_sold_items(query, category, product_id)
            return {'active': active, 'sold': sold}
        except Exception as e:
            self.logger.exception(f"❌ Error during eBay search: {e}")
            return {'active': [], 'sold': []}

    @backoff.on_exception(backoff.expo, ScrapflyScrapeError, max_tries=5)
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        return await self.client.async_scrape(config)

    async def get_active_listings(self, query, category, product_id):
        listings = []
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

        for page in range(1, 4):
            url = f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=12&_pgn={page}"
            self.logger.debug(f"📱 Fetching active listings from eBay page {page}: {url}")

            try:
                resp = await self.scrape_with_retries(ScrapeConfig(
                    url=url,
                    render_js=True,
                    asp=True,
                    wait_for_selector=".s-item__title",
                    session=Utility.generate_random_session(),
                    retry=False,
                    timeout=120000,
                    country="US",
                ))
            except Exception as e:
                self.logger.exception(f"❌ Failed to fetch active listings page {page}: {e}")
                continue

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            for item in soup.select(".s-item"):
                title = item.select_one(".s-item__title")
                price = item.select_one(".s-item__price")
                link = item.select_one("a.s-item__link")
                condition = item.select_one(".SECONDARY_INFO")
                seller = item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")
                bid = item.select_one(".s-item__bids")
                shipping = item.select_one(".s-item__shipping")
                location = item.select_one(".s-item__location")
                image = item.select_one(".s-item__image-img")
                time_left = item.select_one(".s-item__time-left")
                top_rated = item.select_one(".s-item__etrs-text")

                if not title or not price or not link:
                    continue

                t = title.text.strip()
                if t.lower().startswith("shop on ebay") or not t:
                    continue

                detail_url = link['href']

                base_data = {
                    "title": t,
                    "product_id": product_id or str(np.random.randint(100000, 999999)),
                    "listing_price": re.sub(r"[^\d.]", "", price.text.strip()),
                    "bid_count": bid.text.strip() if bid else "0",
                    "url": detail_url,
                    "condition": condition.text.strip() if condition else "Unknown",
                    "seller": seller.text.replace("Seller:", "").strip() if seller else "Unknown",
                    "shipping": shipping.text.strip() if shipping else "Not specified",
                    "location": location.text.strip() if location else "Not specified",
                    "image_url": image["src"] if image and image.has_attr("src") else None,
                    "time_left": time_left.text.strip() if time_left else "Unknown",
                    "top_rated": bool(top_rated),
                    "source": 'ebay'
                }

                try:
                    async with sem:
                        await asyncio.sleep(20)
                        details = await self.get_product_details(detail_url)
                        base_data["description"] = details.get("description")
                        base_data["item_specifics"] = details.get("item_specifics", {})
                        listings.append(base_data)
                        self.insert_or_update_product(base_data, category)
                except Exception as e:
                    self.logger.exception(f"❌ Failed processing item: {e}")

        self.logger.info(f"✅ Active listings fetched and inserted: {len(listings)}")
        return listings

    async def get_sold_items(self, query, category, product_id):
        url = f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=13&LH_Complete=1&LH_Sold=1"
        self.logger.debug(f"📱 Fetching sold items from eBay: {url}")

        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector=".s-item__title",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US",
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch sold listings: {e}")
            return []

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")

        listings = []
        for item in soup.select(".s-item"):
            title = item.select_one(".s-item__title")
            price = item.select_one(".s-item__price")
            link = item.select_one("a.s-item__link")
            condition = item.select_one(".SECONDARY_INFO")
            seller = item.select_one(".s-item__seller-info-text") or item.select_one(".s-item__subtitle")
            date_sold = item.select_one(".s-item__title--tagblock .POSITIVE")
            shipping = item.select_one(".s-item__shipping")
            location = item.select_one(".s-item__location")
            image = item.select_one(".s-item__image-img")
            top_rated = item.select_one(".s-item__etrs-text")

            if not title or not price or not link:
                continue

            t = title.text.strip()
            if t.lower().startswith("shop on ebay") or not t:
                continue

            detail_url = link['href']
            details = await self.get_product_details(detail_url)

            base_data = {
                "title": t,
                "product_id": product_id or str(np.random.randint(100000, 999999)),
                "listing_price": re.sub(r"[^\d.]", "", price.text.strip()),
                "url": link['href'],
                "condition": condition.text.strip() if condition else "Unknown",
                "seller": seller.text.replace("Seller:", "").strip() if seller else "Unknown",
                "shipping": shipping.text.strip() if shipping else "Not specified",
                "location": location.text.strip() if location else "Not specified",
                "image_url": image["src"] if image and image.has_attr("src") else None,
                "top_rated": bool(top_rated),
                "source": 'ebay',
                "description": details.get("description"),
                "item_specifics": details.get("item_specifics", {})
            }

            self.insert_or_update_product(base_data, category)
            listings.append(base_data)

        self.logger.info(f"✅ Sold listings fetched and inserted: {len(listings)}")
        return listings

    async def get_product_details(self, url):
        self.logger.debug(f"🔍 Fetching product detail page: {url}")
        try:
            resp = await self.scrape_with_retries(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="#itemTitle",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US",
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch product detail page: {e}")
            return {}

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")

        def safe_select(selector):
            tag = soup.select_one(selector)
            return tag.text.strip() if tag else None

        item_description = safe_select("#viTabs_0_is") or safe_select("#vi-desc-maincntr")

        item_specs = {}
        for dl in soup.select("dl.ux-labels-values"):
            key_tag = dl.select_one("dt span.ux-textspans")
            value_tag = dl.select_one("dd span.ux-textspans")
            if key_tag and value_tag:
                key = key_tag.get_text(strip=True).rstrip(":")
                value = value_tag.get_text(strip=True)
                if("Exterior Color" in key):
                    key = "Color"
                elif("Color" in key and key != "Color"):
                    key = "Color"
                elif("Size" in key and key != "Size"):
                    key = "Size"
                elif("Brand" in key and key != "Brand"):
                    key = "Brand"
                
                item_specs[key] = value

        return {
            "description": item_description,
            "item_specifics": item_specs
        }

    def insert_or_update_product(self, base_data: dict, category: str):
        # Check if the product with the same URL already exists
        existing = self.product_table.scan(
            FilterExpression=Attr("url").eq(base_data["url"]),

        )

        product = ProductModel(base_data, category)

        if existing["Items"]:
            self.logger.info(f"🔄 Updating existing product in DB: {base_data['url']}")
        else:
            self.logger.info(f"➕ Inserting new product in DB: {base_data['url']}")

        self.product_table.put_item(Item=product.to_dict())

        specs = base_data.get("item_specifics", {})

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
