import os
import requests
from urllib.parse import urlparse
from config.scrapper_config import Config

class GeminiRefiner:
    def refine_query(self, base_query, image_url=None):
        return base_query
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
