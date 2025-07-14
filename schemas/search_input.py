from typing import Optional, List, Dict
from pydantic import BaseModel, HttpUrl

class SearchInput(BaseModel):
    query: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[HttpUrl] = None
    product_id: Optional[str] = None