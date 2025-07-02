class AccessoriesModel:
    def __init__(self, product_id: str, specs: dict):
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.year = specs.get("Year")

    def to_dict(self):
        return self.__dict__