import json
import re
from urllib.parse import quote
from scrapfly import ScrapflyClient, ScrapeConfig
from utils.logger import get_logger
from bs4 import BeautifulSoup
from utils.utility import Utility

class GoogleShoppingScraper:
    def __init__(self, client):
        self.client = client
        self.logger = get_logger()

    async def search(self, query, max_pages=1, max_items=10):
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

        # Try parsing XHR data
        try:
            xhr_calls = resp.scrape_result["browser_data"].get("xhr_call", [])
            api_call = next((c for c in xhr_calls if "googleapis" in c["url"] and "shop" in c["url"]), None)

            if api_call:
                data = json.loads(api_call["response"]["body"])
                items = data.get("items", [])[:max_items]
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

                        results.append({
                            "title": title,
                            "listing_price": str(price),
                            "currency": currency or "USD",
                            "url": link,
                            "image_url": image_url,
                            "condition": "New",
                            "seller": seller,
                            "shipping": "Varies",
                            "location": "Unknown",
                            "top_rated": False,
                            "source": "google_shopping"
                        })
                    except Exception as e:
                        self.logger.warning(f"⚠️ Skipping XHR item due to parse error: {e}")
                if results:
                    self.logger.info(f"✅ Google Shopping XHR results fetched: {len(results)}")
                    return results
        except Exception as e:
            self.logger.warning(f"⚠️ XHR parse failed, falling back to HTML scraping: {e}")

        # Fallback: HTML scraping + save debug HTML
        try:
            html = resp.scrape_result.get("content", "")
            debug_filename = f"debug_google_shopping.html"
            try:
                with open(debug_filename, "w", encoding="utf-8") as f:
                    f.write(html)
                self.logger.info(f"💾 Saved debug HTML to {debug_filename}")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to save debug HTML: {e}")

            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select("div.sh-dlr__list-result")[:max_items]

            for card in cards:
                try:
                    title = card.select_one("h4 > span") or card.select_one("h4")
                    price = card.select_one("span.a8Pemb")
                    link_tag = card.select_one("a.shntl")
                    image = card.select_one("img")
                    seller = card.select_one("div.aULzUe")

                    if not (title and price and link_tag):
                        continue

                    results.append({
                        "title": title.text.strip(),
                        "listing_price": price.text.strip().replace("$", ""),
                        "currency": "USD",
                        "url": "https://www.google.com" + link_tag["href"],
                        "image_url": image["src"] if image and image.has_attr("src") else None,
                        "condition": "New",
                        "seller": seller.text.strip() if seller else "Unknown",
                        "shipping": "Varies",
                        "location": "Unknown",
                        "top_rated": False,
                        "source": "google_shopping"
                    })
                except Exception as e:
                    self.logger.warning(f"⚠️ Skipping HTML item due to parse error: {e}")
        except Exception as e:
            self.logger.exception("❌ Error during HTML fallback parsing")

        self.logger.info(f"✅ Google Shopping final results fetched: {len(results)}")
        return results
