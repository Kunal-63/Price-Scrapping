import os
import requests
from urllib.parse import urlparse
from config.scrapper_config import Config
import backoff
from functools import lru_cache

class GeminiRefiner:
    def __init__(self):
        self.api_key = Config.GEMINI_API_KEY
        self.api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

    @backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=5)
    def call_gemini_api(self, prompt, api_url, api_key):
        response = requests.post(
            api_url,
            headers={"Content-Type": "application/json"},
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("candidates", [])[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "").strip() if parts else ""

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
                filename = ""

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
            "Do not explain or give any extra text. Return nothing else—just the search query like:\n"
            "`nike running shorts black high waisted`\n"
            "or:\n"
            "`chanel quilted leather mini flap bag black`\n"
            "Do NOT return markdown, bullets, or explanations.\n"
        )

        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": Config.GEMINI_API_KEY},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            parts = data.get("candidates", [])[0].get("content", {}).get("parts", [])
            return parts[0].get("text", base_query).strip() if parts else base_query
        except requests.exceptions.RequestException as e:
            print(f"❌ Error using Gemini API: {e}")
            return base_query

    def score_product(self, product: dict, category_metadata: dict, query: str) -> int:
        if not query:
            return 100

        prompt = (
            "You are an intelligent search relevance evaluator. "
            "Given a search query, a product's core details, and its category-specific metadata, return a score from 0 to 100 representing how relevant the product is to the query.\n\n"
            f"Search query: {query}\n\n"
            "Product details:\n"
            f"- Title: {product.get('title', '')}\n"
            f"- Category: {product.get('category', '')}\n"
            f"- Condition: {product.get('condition', '')}\n"
            f"- Seller: {product.get('seller', '')}\n"
            f"- Location: {product.get('location', '')}\n"
            f"- Price: {product.get('listing_price', '')}\n"
            f"- Source: {product.get('source', '')}\n\n"
            "Category metadata:\n"
            + "\n".join(f"- {k}: {v}" for k, v in category_metadata.items() if v) +
            "\n\nOnly return a single integer score from 0 to 100 (no explanation, no words)."
        )

        try:
            response = requests.post(
                self.api_url,
                headers={"Content-Type": "application/json"},
                params={"key": self.api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            parts = data.get("candidates", [])[0].get("content", {}).get("parts", [])
            if parts:
                text = parts[0].get("text", "").strip()
                score = int("".join(filter(str.isdigit, text)))
                return max(0, min(100, score))
        except Exception as e:
            print(f"❌ Gemini scoring error: {e}")

        return 0
    
    def refine_query_with_image(self, product_query: str, image_keywords: str) -> str:
        """Generates the best product search query by combining text and image info using Gemini API."""
        prompt = (
            "You are a product search query expert. "
            "Given a product name and an image filename, merge them intelligently to create the most accurate and searchable product query "
            "for sites like eBay, Grailed, or StockX.\n\n"
            f"Product query: {product_query}\n"
            f"Image filename keywords: {image_keywords}\n\n"
            "🎯 Only return the **final search query** string. No extra text, no explanations, no formatting.\n"
            "Now generate the merged search query:"
        )

        try:
            response = requests.post(
                self.api_url,
                headers={"Content-Type": "application/json"},
                params={"key": self.api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            parts = data.get("candidates", [])[0].get("content", {}).get("parts", [])
            return parts[0].get("text", product_query).strip() if parts else product_query
        except requests.exceptions.RequestException as e:
            print(f"❌ Error using Gemini API: {e}")
            return product_query
        
    @lru_cache(maxsize=5000)
    def extract_size_from_title(self, title: str) -> str:
        if not title:
            return "Not present"

        prompt = (
            "You are an intelligent product metadata extractor. "
            "Given a product title, extract the size from it, if any. "
            "Size can be for clothing (e.g., M, L, XL), shoes (e.g., 9 US), electronics (e.g., 128 GB), or other formats (e.g., 10.5 inch). "
            "If no size is mentioned, respond with 'Not present'.\n\n"
            f"Product title: {title}\n\n"
            "❗Only return the extracted size value or 'Not present'. Do not include units if not explicitly mentioned."
        )

        try:
            result = self.call_gemini_api(prompt, self.api_url, self.api_key)
            return result or "Not present"
        except Exception as e:
            print(f"❌ Gemini size extraction error: {e}")
            return "Not present"
        
    @lru_cache(maxsize=5000)
    def extract_condition_from_title(self, title: str) -> str:
        if not title:
            return "Unknown"

        prompt = (
            "You are an intelligent product metadata extractor. "
            "Given a product title, extract the condition of the product from it. "
            "Only return one of the following: 'New', 'Used', 'Pre-owned', 'Vintage', 'Open box', or 'Unknown'.\n\n"
            f"Product title: {title}\n\n"
            "❗Only return the condition category. Do not explain or return anything else."
        )

        try:
            result = self.call_gemini_api(prompt, self.api_url, self.api_key)
            return result.capitalize() if result else "Unknown"
        except Exception as e:
            print(f"❌ Gemini condition extraction error: {e}")
            return "Unknown"