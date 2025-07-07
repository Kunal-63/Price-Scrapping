from datetime import datetime
import uuid

class ProductModel:
    def __init__(self, base_data: dict, category: str):
        self.product_id = base_data.get("product_id", str(uuid.uuid4()))
        self.title = base_data["title"]
        self.listing_price = base_data["listing_price"]
        self.currency = "USD"
        self.url = base_data["url"]
        self.image_url = base_data.get("image_url")
        self.condition = base_data.get("condition", "Unknown")
        self.seller = base_data.get("seller", "Unknown")
        self.shipping = base_data.get("shipping", "Varies")
        self.location = base_data.get("location", "Unknown")
        self.top_rated = base_data.get("top_rated", False)
        self.source = base_data.get("source")
        self.original_price = base_data.get("original_price")
        self.discount = base_data.get("discount")
        self.category = category
        self.created_at = datetime.utcnow().isoformat()
        self.image_query = base_data.get("image_query")

    def to_dict(self):
        return self.__dict__
