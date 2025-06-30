import json
from scrapfly import ScrapflyClient, ScrapeConfig
from bs4 import BeautifulSoup
from utils.utility import Utility
from utils.logger import get_logger


class VestiaireScraper:
    def __init__(self, scrapfly_key):
        self.client = ScrapflyClient(key=scrapfly_key)
        self.logger = get_logger()

    async def search(self, query="chanel handbags", max_pages=2):
        url = f"https://us.vestiairecollective.com/search/?q={query.replace(' ', '%20')}"
        session_id = Utility.generate_random_session()
        results = []

        try:
            self.logger.info(f"🔍 Searching Vestiaire for: {query}")
            first_page = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                rendering_wait=8000,
                wait_for_selector="script#__NEXT_DATA__",
                country="US",
                session=Utility.generate_random_session(),
                session_sticky_proxy=True,
                retry=False,
                timeout=120000
            ))

            xhr_calls = first_page.scrape_result["browser_data"]["xhr_call"]
            search_call = next((call for call in xhr_calls if "product/search" in call["url"]), None)
            if not search_call:
                self.logger.warning("❌ Couldn't find the product search API call.")
                return []

            initial_data = json.loads(search_call["response"]["body"])
            headers = search_call["headers"]
            payload = json.loads(search_call["body"])
            results_raw = initial_data.get("items", [])
            total_pages = min(initial_data.get("paginationStats", {}).get("totalPages", 1), max_pages)

            self.logger.info(f"✅ Page 1 scraped with {len(results_raw)} items")

            # Additional pages
            for page in range(1, total_pages):
                offset = page * 48
                payload["pagination"]["offset"] = offset

                page_data = await self.client.async_scrape(ScrapeConfig(
                    url="https://search.vestiairecollective.com/v1/product/search",
                    method="POST",
                    headers={
                        **headers,
                        "Referer": url,
                        "Origin": "https://us.vestiairecollective.com",
                        "Content-Type": "application/json"
                    },
                    body=json.dumps(payload),
                    country="US",
                    session=Utility.generate_random_session(),
                    session_sticky_proxy=True,
                    asp=True,
                    timeout=120000,
                    retry=False
                ))

                page_items = json.loads(page_data.scrape_result["content"]).get("items", [])
                results_raw.extend(page_items)
                self.logger.info(f"✅ Page {page + 1} scraped with {len(page_items)} items")

            # Detailed results with per-item fetching
            for item in results_raw:
                try:
                    product_url = f"https://www.vestiairecollective.com{item.get('link', '')}"
                    details = await self.fetch_product_details(product_url, session_id)

                    results.append({
                        "title": f"{item.get('brand', {}).get('name', '')} {item.get('title', '')}".strip(),
                        "listing_price": str(item.get("price", {}).get("amount")),
                        "currency": item.get("price", {}).get("currency", "USD"),
                        "url": product_url,
                        "image_url": item.get("images", [{}])[0].get("url"),
                        "condition": item.get("condition", {}).get("label") or "Unknown",
                        "seller": "Vestiaire Seller",
                        "shipping": "Varies",
                        "location": item.get("seller", {}).get("location", {}).get("country", "Unknown"),
                        "top_rated": False,
                        "source": "vestiaire",
                        "item_details": details
                    })

                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping item due to parse error: {e}")

            self.logger.info(f"🎯 Total detailed results from Vestiaire: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception(f"❌ Vestiaire scraping failed: {e}")
            return []

    async def fetch_product_details(self, url, session_id):
        self.logger.info(f"🔍 Fetching product details from: {url}")
        try:
            resp = await self.client.async_scrape(ScrapeConfig(
                url=url,
                asp=True,
                render_js=True,
                rendering_wait=9000,
                wait_for_selector="div.product-description_description__container__core__s6AGP",
                country="US",
                session=Utility.generate_random_session(),
                session_sticky_proxy=True,
                retry=False,
                timeout=120000,
                auto_scroll=True
            ))

            html = resp.scrape_result["content"]
            soup = BeautifulSoup(html, "html.parser")
            details = {}

            # Description
            desc = soup.select_one("div.product-seller-description_sellerDescription__Lh7HQ p")
            if desc:
                details["description"] = desc.get_text(strip=True)

            # General Info
            for li in soup.select("ul.product-description-list_descriptionList__list__L_0k1 li"):
                try:
                    k = li.select_one("span.product-description-list_descriptionList__property__SKPsZ")
                    v = li.select_one("span.product-description-list_descriptionList__value__rtnBQ")
                    if k and v:
                        key = k.text.strip().replace(":", "").lower().replace(" ", "_")
                        val = v.text.strip().replace("\n", ", ")
                        details[key] = val
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping general info due to error: {e}")

            # Measurements
            for li in soup.select("div.product-description-list_descriptionList__column--right__SNBJj li"):
                try:
                    k = li.select_one("span.product-description-list_descriptionList__property__SKPsZ")
                    v = li.select_one("span.product-description-list_descriptionList__value__rtnBQ")
                    if k and v:
                        key = k.text.strip().replace(":", "").lower().replace(" ", "_")
                        val = v.text.strip()
                        details[key] = val
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping measurement due to error: {e}")

            return details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch product details from {url}: {e}")
            return {}
