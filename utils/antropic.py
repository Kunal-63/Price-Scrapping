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

    @backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=5)
    def call_claude(self, prompt, max_tokens=1024, temperature=0.0):
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # resp.content is a list of TextBlock objects
        if isinstance(resp.content, list):
            # Extract and concatenate all block.text strings
            text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        else:
            text = resp.content or ""
        return text


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
            "Given a product title, extract the size if any. "
            "Size can be clothing (M, L, etc.), shoes (9 US), electronics (128 GB), etc. "
            "If none, return 'Not present'. Only the size.\n"
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
            "Only return one of: 'New', 'Used', "
            "'Pre‑owned', 'Vintage', 'Open box', or 'Unknown'.\n\n"
            f"Product title: {title}\n\n"
        )
        text = self.call_claude(prompt).strip().capitalize()
        return text if text in {"New", "Used", "Pre‑owned", "Vintage", "Open box", "Unknown"} else "Unknown"