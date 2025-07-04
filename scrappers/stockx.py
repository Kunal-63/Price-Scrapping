from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
import re
from utils.logger import get_logger
from utils.utility import Utility
from config.scrapper_config import Config
from boto3.session import Session
from boto3.dynamodb.conditions import Attr
from models.product_item import ProductModel
from models.shoes import ShoesModel
from models.bags import BagsModel
from models.accessories import AccessoriesModel
from models.clothing import ClothingModel
import asyncio

class StockXScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

        session = Session()
        dynamodb = session.resource("dynamodb", region_name="us-east-2")
        self.product_table = dynamodb.Table("ProductsTable")
        self.shoes_table = dynamodb.Table("ShoesTable")
        self.bags_table = dynamodb.Table("BagsTable")
        self.accessories_table = dynamodb.Table("AccessoriesTable")
        self.clothing_table = dynamodb.Table("ClothingTable")

    async def search(self, query, category, product_id):
        self.logger.info(f"🔍 Searching StockX for query: {query}")
        url = f"https://stockx.com/search?s={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector='div[data-testid="ProductTile"]',
                auto_scroll=True,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool"
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            results = []
            for tile in soup.select('div[data-testid="ProductTile"]'):
                title_elem = tile.select_one('[data-testid="product-tile-title"]')
                price_elem = tile.select_one('[data-testid="product-tile-lowest-ask-amount"]')
                link_elem = tile.select_one('a[data-testid="productTile-ProductSwitcherLink"]')
                image_elem = tile.select_one('img[src]')

                if not title_elem or not price_elem or not link_elem:
                    continue

                title = title_elem.text.strip()
                price = price_elem.text.strip().replace("$", "")
                link = f"https://stockx.com{link_elem['href']}"
                image_url = image_elem["src"] if image_elem else None

                # recent_sales = await self.fetch_sales_history(link)
                recent_sales = {}
                # item_details = await self.get_product_details(link)
                item_details = {}

                results.append({
                    "title": title,
                    "listing_price": price,
                    "product_id": product_id,
                    "category": category,
                    "url": link,
                    "image_url": image_url,
                    "condition": "New" if "new" in title.lower() else "Unknown",
                    "seller": "StockX Verified",
                    "shipping": "Varies (via StockX)",
                    "location": "US (via StockX)",
                    "top_rated": True,
                    "sales_history": recent_sales,
                    "item_details": item_details,
                    "source": "stockx"
                })

            await asyncio.gather(*[
                asyncio.to_thread(self.insert_or_update_product, item, category)
                for item in results
            ])

            self.logger.info(f"✅ StockX results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception("❌ Failed to scrape StockX")
            return []

    async def fetch_sales_history(self, url):
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=False,
                render_js=True,
                wait_for_selector="tr",
                js="""
                    setTimeout(() => {
                        const buttons = document.querySelectorAll('button, div[role="button"]');
                        for (let btn of buttons) {
                            if (btn.textContent && (
                                btn.textContent.includes('Market Data') ||
                                btn.textContent.includes('View All Sales') ||
                                btn.textContent.includes('Sales History')
                            )) {
                                btn.click();
                                break;
                            }
                        }
                    }, 3000);
                """,
                auto_scroll=True,
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                timeout=120000,
                retry=False,
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            rows = soup.find_all("tr")
            data = []
            for r in rows:
                cols = r.find_all("td")
                if len(cols) >= 2:
                    date = cols[0].text.strip()
                    price = cols[1].text.strip()
                    if "$" in price:
                        data.append({"date": date, "price": price})

            return data

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch sales history for {url}: {e}")
            return []

    async def get_product_details(self, url):
        self.logger.info(f"🔍 Fetching item details from StockX: {url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=False,
                render_js=True,
                wait_for_selector="section[data-testid='product-detail']",
                country="US",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                timeout=120000,
                retry=False,
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")

            details = {}
            for group in soup.select("section[data-testid='product-detail'] div[data-testid='detail']"):
                key_el = group.select_one("div[data-testid='label']")
                val_el = group.select_one("div[data-testid='value']")
                if key_el and val_el:
                    key = key_el.text.strip().lower().replace(" ", "_").replace(":", "")
                    val = val_el.text.strip()
                    details[key] = val

            # Extract any extra bullet point description
            description = []
            for li in soup.select("ul li"):
                text = li.get_text(strip=True)
                if text:
                    description.append(text)

            if description:
                details["description"] = description

            return details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch product details for {url}: {e}")
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
