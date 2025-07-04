import uuid
import datetime

class ClothingModel:
    def __init__(self, product_id: str, specs: dict):
        self.id = f"{product_id}"
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.size = specs.get("Size")
        self.created_at = datetime.datetime.utcnow().isoformat()


    def to_dict(self):
        return self.__dict__