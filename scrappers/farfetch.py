import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger
import json
import asyncio

class FarfetchScraper:
    def __init__(self, client):
        self.client = client
        self.logger = get_logger()

    async def search(self, query, max_pages=1, max_items=10):
        self.logger.info(f"🔍 Searching Farfetch for query: {query}")
        url = f"https://www.farfetch.com/shopping/men/items.aspx?query={quote(query)}"

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                wait_for_selector="article[data-testid='product-card']",
                country="US",
                session=Utility.generate_random_session(),
                retry=False,
                timeout=120000
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Farfetch listings: {e}")
            return []

        html = resp.scrape_result.get("content", "")
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("article[data-testid='product-card']")

        results = []
        detail_tasks = []

        for item in items[:max_items]:
            try:
                brand = item.select_one("p[data-component='ProductCardBrandName']")
                title = item.select_one("p[data-component='ProductCardDescription']")
                price_el = item.select_one("p[data-component='PriceFinal']")
                link_tag = item.select_one("a[data-component='ProductCardLink']")
                image_tag = item.select_one("link[itemprop='image']")

                if brand and title and price_el and link_tag:
                    price = re.sub(r"[^\d.]", "", price_el.text)
                    product_url = f"https://www.farfetch.com{link_tag['href']}"
                    image_url = image_tag["href"] if image_tag else None

                    results.append({
                        "title": f"{brand.text.strip()} {title.text.strip()}",
                        "listing_price": price,
                        "url": product_url,
                        "image_url": image_url,
                        "condition": "New",
                        "seller": "Farfetch",
                        "shipping": "Varies by region",
                        "location": "Global",
                        "top_rated": True,
                        "source": "farfetch"
                    })
                    detail_tasks.append(self.get_product_details(product_url))
                else:
                    # In case of parsing issue, add dummy detail task
                    detail_tasks.append(asyncio.sleep(0, result={}))

            except Exception as e:
                self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")
                detail_tasks.append(asyncio.sleep(0, result={}))

        self.logger.info(f"🧵 Fetching {len(detail_tasks)} product details concurrently...")

        detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

        for item, detail in zip(results, detail_results):
            item["item_details"] = detail if isinstance(detail, dict) else {}

        self.logger.info(f"✅ Farfetch results fetched: {len(results)}")
        return results

    async def get_product_details(self, product_url):
        self.logger.info(f"🔍 Fetching Farfetch product details: {product_url}")

        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=product_url,
                asp=True,
                render_js=True,
                wait_for_selector="div[data-component='AccordionPanel'][data-expanded='true']",
                js="""
                    (() => {
                        const buttons = document.querySelectorAll("[data-component='AccordionButton']");
                        for (let btn of buttons) {
                            if (btn.innerText.includes("The Details") && btn.getAttribute("data-expanded") === "false") {
                                btn.click();
                                break;
                            }
                        }
                    })();
                """,
                rendering_stage="complete",
                country="US",
                session=Utility.generate_random_session(),
                timeout=120000,
                retry=False
            ))
        except Exception as e:
            self.logger.exception(f"❌ Failed to fetch Farfetch product detail page: {e}")
            return {}

        html = resp.scrape_result.get("content", "")

        try:
            with open('farfetch_detail_debug.html', 'w', encoding='utf-8') as f:
                f.write(html)
        except Exception as file_error:
            self.logger.warning(f"⚠️ Could not save HTML for debugging: {file_error}")

        return self.extract_fetched_details(html)

    def extract_fetched_details(self, html):
        soup = BeautifulSoup(html, "html.parser")
        
        # Find the expanded panel
        panel = soup.find("div", {"data-component": "AccordionPanel", "data-expanded": "true"})
        if not panel:
            return {}

        details = {}

        # Brand & title
        heading = panel.select_one("a[data-component='HeadingBold']")
        subheading = panel.select_one("p[data-component='Body']")
        description = panel.select("div[data-component='Body'] > p[data-component='Body']")
        details["brand"] = heading.text.strip() if heading else ""
        details["title"] = subheading.text.strip() if subheading else ""
        if description:
            details["description"] = " ".join([p.text.strip() for p in description])

        # Highlights
        highlights_section = panel.find("h4", string=re.compile("Highlights", re.I))
        if highlights_section:
            ul = highlights_section.find_next("ul")
            if ul:
                details["highlights"] = [li.text.strip() for li in ul.find_all("li")]

        # Composition
        composition_section = panel.find("h4", string=re.compile("Composition", re.I))
        if composition_section:
            p = composition_section.find_next("p")
            if p:
                details["composition"] = p.text.strip()

        # Washing Instructions
        wash_section = panel.find("h4", string=re.compile("Washing instructions", re.I))
        if wash_section:
            p = wash_section.find_next("p")
            if p:
                details["washing_instructions"] = p.text.strip()

        # Farfetch ID
        farfetch_id_el = panel.find(string=re.compile("FARFETCH ID", re.I))
        if farfetch_id_el and farfetch_id_el.parent:
            details["farfetch_id"] = farfetch_id_el.parent.text.strip().replace("FARFETCH ID:", "").strip()

        return details