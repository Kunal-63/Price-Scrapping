from pydantic import BaseModel, HttpUrl
from typing import Optional


class ProductItem(BaseModel):
    title: str
    listing_price: str
    currency: Optional[str] = "USD"
    url: HttpUrl
    image_url: Optional[HttpUrl]
    condition: Optional[str] = "Unknown"
    seller: Optional[str] = "Unknown"
    shipping: Optional[str] = "Varies"
    location: Optional[str] = "Unknown"
    top_rated: Optional[bool] = False
    source: str
    original_price: Optional[str]
    discount: Optional[str]

    class Config:
        orm_mode = True
