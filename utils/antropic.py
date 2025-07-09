from decimal import Decimal
import os
from urllib.parse import urlparse
import requests
import backoff
from functools import lru_cache
import anthropic

class AnthropicRefiner:
    def __init__(self):
        self.api_key = "os.environ.get("ANTHROPIC_API_KEY", "")"
        self.client = anthropic.Anthropic(api_key=self.api_key)
        # Use whichever Claude model suits your needs
        self.model = "claude-3-5-sonnet-20240620"

    @backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=2)
    def call_claude(self, prompt, max_tokens=1024, temperature=0.0):
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            if isinstance(resp.content, list):
                return "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
            return (resp.content or "").strip()
        except Exception as e:
            print(f"⚠️ Claude API error: {e}")
            return ""


    def refine_query(self, base_query, image_url=None):
        if not base_query:
            print("❌ No base query provided.")
            return ""

        filename = ""
        if image_url:
            try:
                filename = os.path.basename(urlparse(image_url).path).replace('-', ' ').replace('_', ' ')
            except Exception as e:
                print(f"⚠️ Error parsing image URL: {e}")
        
        prompt = (
            "You are a search optimization expert. "
            "Given a basic product name and optionally an image file name, return a clean and specific product search query "
            "that would work best for an online marketplace like Farfetch or eBay.\n\n"
            f"Base name: {base_query}\n"
        )
        if filename:
            prompt += f"Image file name: {filename}\n"
        prompt += (
            "\n❗IMPORTANT: Only return the final search query string. "
            "Do not explain or give any extra text. "
            "Return nothing else—just the search query.\n"
        )

        return self.call_claude(prompt).strip() or base_query

    def score_product(self, product: dict, category_metadata: dict, query: str) -> int:
        if not query:
            return 100

        def sanitize(val):
            if isinstance(val, (str, int, float, Decimal)):
                return str(val)
            return ""

        product_lines = [f"- {k}: {sanitize(v)}" for k, v in product.items()]
        metadata_lines = [f"- {k}: {sanitize(v)}" for k, v in category_metadata.items()]

        prompt = (
            "You are an intelligent search relevance evaluator.\n\n"
            "Your task is to evaluate how relevant a product is to a user's search query.\n"
            "Return an integer between 0 and 100 that reflects how well the product matches the query.\n\n"
            f"🔍 Search Query:\n{query}\n\n"
            f"📦 Product Details:\n" + "\n".join(product_lines) + "\n\n"
            f"🗂️ Category Metadata:\n" + "\n".join(metadata_lines) + "\n\n"
            "🎯 Output ONLY a single integer from 0 to 100. No explanation, no formatting, no extra text."
        )

        try:
            text = self.call_claude(prompt).strip()
            score = int("".join(filter(str.isdigit, text.split()[0])))  # Only use first token
            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ Scoring failed for query '{query}': {e}")
        return 0

    def refine_query_with_image(self, product_query: str, image_keywords: str) -> str:
        prompt = (
            "You are a product search query expert. "
            "Given a product name and an image filename, merge them intelligently to create the most accurate and searchable product query "
            "for sites like eBay, Grailed, or StockX.\n\n"
            f"Product query: {product_query}\n"
            f"Image filename keywords: {image_keywords}\n\n"
            "Only return the final search query string. No extra text.\n"
        )
        return self.call_claude(prompt).strip() or product_query

    @lru_cache(maxsize=5000)
    def extract_size_from_title(self, title: str) -> str:
        if not title:
            return "Not present"

        prompt = (
            "Extract only the US-based size from the following product title. "
            "Return only the clean size value — remove any extra words, labels, or descriptors. "
            "Do NOT include gender labels (like Men's, Women's, Kids), brand names, or non-US sizes (like EU 40, UK 8, IT 50). "
            "Only return the clean US size value (e.g., 9 US, M, Large, 32x32, etc). If no valid size is found, return exactly: Not present.\n\n"
            "Examples:\n"
            "- 'Nike Air Max 90 Shoes Size 9 US' → 9 US\n"
            "- 'Gucci T-shirt - Size M' → M\n"
            "- 'Chanel Medium Classic Flap Bag' → Medium\n"
            "- 'Versace Gallery Silk Shirt' → Not present\n"
            "- 'Nike Air Force 1 EU 40 / US 9 Men's' → 9 US\n"
            "- 'Yeezy Boost 350 V2 UK 8 / US 9 Women's' → 9 US\n"
            "- 'Polo Shirt Size Large Men's' → Large\n"
            "- 'Levi’s Jeans 32x32 Men’s' → 32x32\n"
            "- 'Rolex Submariner Watch' → Not present\n\n"
            f"Product title: {title}\n"
        )

        return self.call_claude(prompt).strip() or "Not present"

    @lru_cache(maxsize=5000)
    def extract_condition_from_title(self, title: str) -> str:
        if not title:
            return "Unknown"
        prompt = (
            "You are an intelligent product metadata extractor. "
            "Given a product title, extract the condition of the product from it. "
            "Only return one of: 'New', 'Like New', "
            "'Excellent', 'Good', 'Fair', or 'Varies'.\n\n"
            f"Product title: {title}\n\n"
        )
        text = self.call_claude(prompt).strip().capitalize()
        return text if text in {"New", "Like New", "Excellent", "Good", "Fair", "Varies"} else "Varies"
    
    def refine_location(self, location: str) -> str:
        if not location or location.lower() in {"not present", "unknown", "n/a"}:
            return "Not present"

        prompt = (
            "You are a geographic standardizer.\n"
            "Given a raw or informal location, return the most accurate, standardized location name "
            "(e.g., city, state, country) in a readable format.\n"
            "Do not add extra explanation. Only return the refined location.\n"
            "If the input is not a location, return exactly: Not present.\n\n"
            "Examples:\n"
            "- 'nyc' → New York City\n"
            "- 'USA' → United States\n"
            "- 'delhi india' → Delhi, India\n"
            "- 'Not present' → Not present\n"
            "- 'S.F.' → San Francisco\n\n"
            f"Raw location: {location}"
        )

        return self.call_claude(prompt).strip() or "Not present"
    
    def extract_location_from_title(self, title: str) -> str:
        if not title:
            return "Not present"

        prompt = (
            "Extract the geographic location from the following product title, if any. "
            "This could be a city, state, country, or region mentioned in the title. "
            "Return only the location with no extra text. If no location is present, return exactly: Not present.\n\n"
            "Examples:\n"
            "- 'Nike Air Max 90 - New York Edition' → New York\n"
            "- 'Louis Vuitton Bag - Made in France' → France\n"
            "- 'Vintage Levi's Jeans USA' → USA\n"
            "- 'Rolex Submariner Watch' → Not present\n\n"
            f"Product title: {title}\n"
        )

        return self.call_claude(prompt).strip() or "Not present"