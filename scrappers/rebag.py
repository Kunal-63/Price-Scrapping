#NO ITEM DETAILS FOUND
import random
import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
import asyncio

class RebagScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query, max_items=10):
        self.logger.info(f"🔍 Searching Rebag for query: {query}")
        url = f"https://shop.rebag.com/collections/all?q={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="div.plp__product",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception("❌ Rebag scraping failed")
            return []

        soup = BeautifulSoup(resp.scrape_result["content"], "html.parser")
        cards = soup.select("div.plp__product")[:max_items]

        results = []
        detail_tasks = []

        for card in cards:
            try:
                anchor = card.find("a", class_="plp__card")
                if not anchor:
                    continue

                title_el = card.select_one(".products-carousel__card-title")
                brand_el = card.select_one(".products-carousel__card-designer")
                price_el = card.select_one(".rewards-plus-plp__product-price-value")
                image_el = card.select_one(".product-card-image__img, .products-carousel__image-wrapper img")

                title = f"{brand_el.get_text(strip=True)} {title_el.get_text(strip=True)}" if brand_el and title_el else None
                price = re.sub(r"[^\d.]", "", price_el.get_text(strip=True)) if price_el else None
                href = anchor["href"] if anchor.has_attr("href") else None
                image_url = "https:" + image_el["data-src"] if image_el and image_el.has_attr("data-src") else None

                if not (title and price and href):
                    continue

                full_url = href if href.startswith("http") else "https://shop.rebag.com" + href

                results.append({
                    "title": title,
                    "listing_price": price,
                    "url": full_url,
                    "image_url": image_url,
                    "condition": "Used",
                    "seller": "Rebag Verified",
                    "shipping": "Free Shipping in US",
                    "location": "US",
                    "top_rated": True,
                    "source": "rebag"
                })

                detail_tasks.append(self.get_product_details(full_url))
            except Exception as e:
                self.logger.warning(f"⚠️ Skipping Rebag item due to error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching {len(detail_tasks)} product details concurrently...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for result, detail in zip(results, detail_results):
            result["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ Rebag results fetched: {len(results)}")
        return results

    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching product details from Rebag: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector=".pdp__container",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception("❌ Failed to fetch Rebag product detail page")
            return {}

        try:
            soup = BeautifulSoup(resp.scrape_result.get("content", ""), "html.parser")
            details = {}

            # Product details in bullet points
            bullet_section = soup.select_one(".accordion__body ul")
            if bullet_section:
                bullets = [li.get_text(strip=True) for li in bullet_section.select("li")]
                details["description"] = bullets

            # Extract dimensions or specific attributes if present
            attr_section = soup.select(".product-attributes__list li")
            for attr in attr_section:
                key = attr.select_one(".product-attributes__label")
                value = attr.select_one(".product-attributes__value")
                if key and value:
                    details[key.text.strip().lower().replace(" ", "_")] = value.text.strip()

            return details

        except Exception as e:
            self.logger.exception("❌ Failed to parse Rebag product detail content")
            return {}
