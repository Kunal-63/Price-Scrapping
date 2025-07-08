from datetime import datetime
from decimal import Decimal
import uuid

class ProductModel:
    def __init__(self, base_data: dict, category: str):
        self.product_id = base_data.get("product_id", str(uuid.uuid4()))
        self.title = base_data.get("title", "")
        self.listing_price = Decimal(str(base_data.get("listing_price", 0)))
        self.currency = base_data.get("currency", 'USD')
        self.url = base_data.get("url", "")
        self.condition = base_data.get("condition", "Unknown")
        self.seller = base_data.get("seller", "Unknown")
        self.location = base_data.get("location", "Unknown")
        self.top_rated = base_data.get("top_rated", False)
        self.source = base_data.get("source", "unknown")
        self.size = base_data.get("size")
        self.category = category
        self.created_at = datetime.utcnow().isoformat()

    def to_dict(self):
        return self.__dict__
