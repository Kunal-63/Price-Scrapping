import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
import asyncio

class GrailedScraper:
    def __init__(self, client):
        self.client = client
        self.logger = get_logger()

    async def search(self, query, max_items=10):
        self.logger.info(f"🔍 Searching Grailed for query: {query}")
        url = f"https://www.grailed.com/shop?query={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector="div.UserItem_root__8Q2R_",
                country="US",
                retry=False,
                timeout=120000
            ))
        except Exception as e:
            self.logger.exception(f"❌ Grailed scrape failed: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("div.UserItem_root__8Q2R_")
        self.logger.info(f"🔍 Found {len(items)} items on Grailed")

        results = []
        detail_tasks = []

        for item in items[:max_items]:
            try:
                anchor = item.select_one("a.UserItem_link__kgEWg")
                if not anchor:
                    continue

                title_el = anchor.select_one("p.UserItem_title__riOTf")
                designer_el = anchor.select_one("p.UserItem_designer__N8CxZ")
                price_el = item.select_one("span[data-testid='Current']")
                orig_price_el = item.select_one("span[data-testid='Original']")
                discount_el = item.select_one("span[data-testid='PercentOff']")
                image_el = anchor.select_one("img")
                href = anchor.get("href")

                if not (title_el and price_el and href):
                    continue

                title = title_el.text.strip()
                designer = designer_el.text.strip() if designer_el else ""
                price = re.sub(r"[^\d.]", "", price_el.text)
                original_price = re.sub(r"[^\d.]", "", orig_price_el.text) if orig_price_el else None
                percent_off = discount_el.text.strip() if discount_el else None
                image_url = "https:" + image_el["src"] if image_el and image_el.has_attr("src") else None
                full_url = f"https://www.grailed.com{href}"

                results.append({
                    "title": f"{designer} {title}".strip(),
                    "listing_price": price,
                    "original_price": original_price,
                    "discount": percent_off,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": "Used",
                    "seller": "Grailed Seller",
                    "shipping": "Varies",
                    "location": "US",
                    "top_rated": False,
                    "source": "grailed"
                })
                detail_tasks.append(self.get_product_details(full_url))
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching {len(detail_tasks)} item details concurrently...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ Grailed results fetched: {len(results)}")
        return results


    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching Grailed item details: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                asp=True,
                render_js=True,
                wait_for_selector="div.MainContent_item__dpT9l",
                timeout=120000,
                retry=False,
                country="US",
                session=Utility.generate_random_session()
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch item detail page: {e}")
            return {}

        try:
            html = resp.scrape_result.get("content", "")
            soup = BeautifulSoup(html, "html.parser")
            container = soup.select_one("div.MainContent_item__dpT9l.MainContent_details__Co188")
            if not container:
                self.logger.warning("⚠️ Product details container not found")
                return {}

            # Extract designers
            designer_tags = container.select("p.Details_designers__AG6hs a.Designers_designer__Mbyk_")
            designers = " × ".join([d.text.strip() for d in designer_tags]) if designer_tags else ""

            # Title
            title_tag = container.select_one("h1.Details_title__8rdLK")
            title = title_tag.text.strip() if title_tag else ""

            # Size
            size_tag = container.select_one("p[data-testid='non-mobile-size'] span.Details_value__XcEpo")
            size = size_tag.text.strip() if size_tag else ""

            # Color
            color_tag = container.select_one("span.Color_value__TrEnh")
            color = color_tag.text.strip() if color_tag else ""

            # Condition
            condition_tag = container.select_one("p:contains('Condition') span.Details_value__XcEpo")
            if not condition_tag:
                condition_tag = container.select_one("p.Details_detail__7xjgu span.Details_value__XcEpo")
            condition = condition_tag.text.strip() if condition_tag else ""

            details = {
                "designers": designers,
                "title": title,
                "size": size,
                "color": color,
                "condition": condition,
                "source": "grailed"
            }
            return details

        except Exception as e:
            self.logger.exception(f"❌ Failed to parse item details: {e}")
            return {}