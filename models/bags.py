class BagsModel:
    def __init__(self, product_id: str, specs: dict):
        self.product_id = product_id
        self.brand = specs.get("Brand")
        self.condition = specs.get("Condition")
        self.year = specs.get("Year")
        self.size = specs.get("Size")
        self.color = specs.get("Color")
        self.box_papers = specs.get("Box/Papers")
        self.dust_bag = specs.get("Dust Bag")

    def to_dict(self):
        return self.__dict__
