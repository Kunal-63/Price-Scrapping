class ShoesModel:
    def __init__(self, product_id: str, specs: dict):
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.year = specs.get("Year")
        self.colorway = specs.get("Colorway")
        self.size = specs.get("Size")
        self.box_papers = specs.get("Box/Papers")

    def to_dict(self):
        return self.__dict__
