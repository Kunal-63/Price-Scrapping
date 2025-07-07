import uuid
import datetime

class JewelryModel:
    def __init__(self, product_id: str, specs: dict, url: str):
        self.id = f"{product_id}"
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.metal = specs.get("Metal")
        self.stone = specs.get("Stone")
        self.box = specs.get("Box")
        self.created_at = datetime.datetime.utcnow().isoformat()
        self.url = url


    def to_dict(self):
        return self.__dict__