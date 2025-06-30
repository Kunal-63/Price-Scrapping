from config.scrapper_config import Config
import requests


class ImageSearcher:
    def reverse_image_search(self, image_url):
        params = {
            "engine": "google_reverse_image",
            "image_url": image_url,
            "api_key": Config.SERPAPI_KEY
        }
        try:
            response = requests.get("https://serpapi.com/search", params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching SerpAPI: {e}")
            return None

        return (
            data.get("search_information", {}).get("query_displayed") or
            data.get("knowledge_graph", {}).get("title") or
            data.get("visual_matches", [{}])[0].get("title")
        )