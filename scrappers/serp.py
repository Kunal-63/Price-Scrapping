import re
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from config.scrapper_config import Config
from utils.logger import get_logger

class SerpAPISearcher:
    def __init__(self):
        self.logger = get_logger()

    async def search(self, query, max_items=10):
        self.logger.info(f"🔍 Searching SerpAPI for query: {query}")

        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": Config.SERPAPI_KEY,
            "hl": "en",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get("https://serpapi.com/search.json", params=params)
                res.raise_for_status()
                products = res.json().get("shopping_results", [])[:max_items]

            results = []
            detail_tasks = []

            for p in products:
                title = p.get("title")
                price = p.get("price")
                link = p.get("product_link")
                condition = p.get("condition") or "Unknown"
                seller = p.get("source") or "Unknown"
                shipping = p.get("shipping", {}).get("price") if isinstance(p.get("shipping"), dict) else "Not specified"
                location = p.get("extracted_location") if "extracted_location" in p else "Unknown"
                image_url = p.get("thumbnail")

                if not (title and link and price):
                    continue

                results.append({
                    "title": title,
                    "listing_price": re.sub(r"[^\d.]", "", price),
                    "url": link,
                    "condition": condition,
                    "seller": seller,
                    "shipping": shipping,
                    "location": location,
                    "image_url": image_url,
                    "top_rated": False,
                    "source": "google_shopping"
                })
                detail_tasks.append(self.get_product_details(link))

            self.logger.info(f"🧵 Fetching product detail pages concurrently...")
            detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

            for i, detail in enumerate(detail_results):
                results[i]["item_details"] = detail if isinstance(detail, dict) else {}

            self.logger.info(f"✅ Google Shopping results fetched: {len(results)}")
            return results

        except Exception as e:
            self.logger.exception("❌ SerpAPI Shopping request failed")
            return []

    async def get_product_details(self, url):
        self.logger.info(f"🔍 Fetching product details from Google Shopping URL: {url}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(url, headers=headers)
                res.raise_for_status()
                soup = BeautifulSoup(res.text, "html.parser")

            description = []
            details = {}

            for li in soup.select("ul, .product-details, .specs, .a-unordered-list")[:3]:
                for item in li.select("li"):
                    text = item.get_text(strip=True)
                    if text:
                        description.append(text)

            for row in soup.select("table tr"):
                cols = row.select("td, th")
                if len(cols) == 2:
                    key = cols[0].text.strip().lower().replace(" ", "_").replace(":", "")
                    val = cols[1].text.strip()
                    details[key] = val

            details["description"] = description
            return details

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch/parse product details from: {url} | {e}")
            return {}
