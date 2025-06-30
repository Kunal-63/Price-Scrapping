import re
from urllib.parse import quote, urljoin
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.logger import get_logger
from utils.utility import Utility
import asyncio

class FashionphileScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query, max_items=10):
        self.logger.info(f"🔍 Searching Fashionphile for query: {query}")
        url = f"https://www.fashionphile.com/shop?search={quote(query)}"
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                render_js=True,
                asp=True,
                wait_for_selector="li.ais-Hits-item",
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                retry=False,
                timeout=120000,
                country="US"
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Fashionphile listings: {e}")
            return []

        html = resp.scrape_result["content"]
        soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("li", class_="ais-Hits-item")

        results = []
        detail_tasks = []

        for card in items[:max_items]:
            try:
                link_tag = card.find("a", class_="linkHover")
                title_tag = card.find("a", class_="hitTitle")
                brand_tag = card.find("span", class_="brand")
                price_tag = card.find("span", itemprop="price")
                image_tag = link_tag.find("img") if link_tag else None
                condition_tag = card.find("div", class_="condition-container")

                if not (link_tag and title_tag and brand_tag and price_tag):
                    continue

                title = f"{brand_tag.text.strip()} {title_tag.text.strip()}"
                price = re.sub(r"[^\d.]", "", price_tag.text.strip())
                url_full = urljoin("https://www.fashionphile.com", link_tag["href"])
                image_url = image_tag["src"] if image_tag and image_tag.has_attr("src") else None
                condition = condition_tag.text.strip() if condition_tag else "Unknown"

                results.append({
                    "title": title,
                    "listing_price": price,
                    "url": url_full,
                    "image_url": image_url,
                    "condition": condition,
                    "seller": "Fashionphile Verified",
                    "shipping": "Free Shipping in US",
                    "location": "US",
                    "top_rated": True,
                    "source": "fashionphile"
                })
                detail_tasks.append(self.get_product_details(url_full))

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to parse a product card: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching item details concurrently for {len(detail_tasks)} products...")
        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ Fashionphile results fetched: {len(results)}")
        return results
    
    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching Fashionphile item details: {product_url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                render_js=True,
                asp=True,
                wait_for_selector="div.accordionPanel",
                js="""
                    setTimeout(() => {
                        document.querySelectorAll('button.pmButton[aria-label="Open"]')
                          .forEach(btn => btn.click());
                    }, 1000);
                """,
                session=Utility.generate_random_session(),
                proxy_pool="public_residential_pool",
                country="US",
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch item detail page: {e}")
            return {}

        html = resp.scrape_result.get("content", "")
        return self.extract_item_details(html)

    def extract_item_details(self, html):
        soup = BeautifulSoup(html, "html.parser")
        accordion_panels = soup.find_all("div", class_="accordionPanel")
        details = {}

        for panel in accordion_panels:
            for p in panel.find_all("p"):
                text = p.text.strip()
                if ":" in text:
                    key, value = map(str.strip, text.split(":", 1))
                    details[key.lower()] = value
                else:
                    if "description" not in details:
                        details["description"] = text
                    else:
                        details["description"] += f" {text}"

        return details