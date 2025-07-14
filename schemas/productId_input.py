from typing import Optional, List, Dict
from pydantic import BaseModel, HttpUrl

class ProductIdInput(BaseModel):
    product_id: Optional[str] = None
    query: Optional[str] = None
    filters: Optional[Dict[str, str]] = None
    limit: Optional[int] = 10
    last_evaluated_key: Optional[Dict] = None
    group_by: Optional[str] = None
    sort_by: Optional[str] = None  # New
    sort_order: Optional[str] = "asc"  # "asc" or "desc"

