import uuid
import datetime

class ShoesModel:
    def __init__(self, product_id: str, specs: dict):
        self.id = f"{product_id}"
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.year = specs.get("Year")
        self.colorway = specs.get("Colorway")
        self.size = specs.get("Size")
        self.box_papers = specs.get("Box/Papers")
        self.created_at = datetime.datetime.utcnow().isoformat()

    def to_dict(self):
        return self.__dict__
