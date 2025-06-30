from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from bs4 import BeautifulSoup
import re
from urllib.parse import quote
from utils.logger import get_logger
from utils.utility import Utility
import asyncio
import backoff

class EbayScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()
        self.MAX_CONCURRENT_DETAIL_FETCHES = 1

    async def search(self, query):
        self.logger.info(f"🔍 Starting eBay search for: {query}")
        try:
            active = await self.get_active_listings(query)
            sold = await self.get_sold_items(query)
            self.logger.info(f"✅ eBay search complete: {len(active)} active, {len(sold)} sold listings found.")
            return {'active': active, 'sold': sold}
        except Exception as e:
            self.logger.exception(f"❌ Error during eBay search: {e}")
            return {'active': [], 'sold': []}
        
    @backoff.on_exception(backoff.expo, ScrapflyScrapeError, max_tries=5)
    async def scrape_with_retries(self, config):
        self.logger.debug(f"⏳ Scraping URL: {config.url}")
        return await self.client.async_scrape(config)

    async def get_active_listings(self, query):
        url = f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=12"
        self.logger.debug(f"📡 Fetching active listings from eBay: {url}")

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
                # wait_for_browser=True                
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch active listings: {e}")
            return []

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")

        items = []
        tasks = []
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

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
            tasks.append(self._fetch_with_details(detail_url, base_data, sem))

        listings = await asyncio.gather(*tasks)
        self.logger.info(f"✅ Active listings fetched: {len(listings)}")
        return listings
    
    async def _fetch_with_details(self, url, base_data, sem):
        async with sem:
            details = await self.get_product_details(url)
            base_data["description"] = details.get("description")
            base_data["item_specifics"] = details.get("item_specifics", {})
            return base_data
        
    async def get_sold_items(self, query):
        url = f"https://www.ebay.com/sch/i.html?_nkw={quote(query)}&_sop=13&LH_Complete=1&LH_Sold=1"
        self.logger.debug(f"📡 Fetching sold items from eBay: {url}")

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
                # wait_for_browser=True
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

            listings.append({
                "title": t,
                "sold_price": re.sub(r"[^\d.]", "", price.text.strip()),
                "date_sold": date_sold.text.strip() if date_sold else "Unknown",
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
            })

        self.logger.info(f"✅ Sold listings fetched: {len(listings)}")
        return listings

    def merge_listings(self, active, sold):
        combined = []
        for act in active:
            entry = {
                "title": act["title"],
                "listing_price": act.get("listing_price"),
                "bid_count": act.get("bid_count"),
                "condition": act.get("condition"),
                "seller": act.get("seller"),
                "url": act.get("url"),
                "sold_price": None,
                "sold_date": None
            }
            for s in sold:
                if s["title"].lower() in act["title"].lower() or act["title"].lower() in s["title"].lower():
                    entry["sold_price"] = s["sold_price"]
                    entry["sold_date"] = s["date_sold"]
                    break
            combined.append(entry)
        self.logger.debug(f"🧩 Merged {len(combined)} listings with sold data.")
        return combined

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
                timeout=60000,
                country="US",
                # wait_for_browser=True
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch product detail page: {e}")
            return {}

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")

        def safe_select(selector):
            tag = soup.select_one(selector)
            return tag.text.strip() if tag else None

        # Basic description
        item_description = safe_select("#viTabs_0_is") or safe_select("#vi-desc-maincntr")

        # 🧠 Extract item specifics from modern layout
        item_specs = {}
        for dl in soup.select("dl.ux-labels-values"):
            key_tag = dl.select_one("dt span.ux-textspans")
            value_tag = dl.select_one("dd span.ux-textspans")
            if key_tag and value_tag:
                key = key_tag.get_text(strip=True).rstrip(":")
                value = value_tag.get_text(strip=True)
                item_specs[key] = value

        return {
            "description": item_description,
            "item_specifics": item_specs
        }