# app/api/search_and_store.py
from fastapi import APIRouter, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import async_session
from models.product_item import Product
from schemas.product_item import ProductItem
from sqlalchemy.future import select
import asyncio

from utils.product_engine import ProductSearchEngine
from utils.logger import get_logger

router = APIRouter()
logger = get_logger()

class SearchInput(BaseModel):
    query: Optional[str] = None
    image_url: Optional[HttpUrl] = None


@router.post("/search/")
async def search_and_store(input_data: SearchInput):
    if not input_data.query and not input_data.image_url:
        return {"error": "Provide either 'query' or 'image_url'."}

    engine = ProductSearchEngine()

    # Do the product search
    try:
        combined = await engine.search(query=input_data.query, image_url=input_data.image_url)
    except Exception as e:
        logger.exception("Search failed")
        return {"error": str(e)}

    if not combined:
        return {"message": "No results found"}

    # Flatten all results
    all_items = []
    for source, items in combined.items():
        print(source, items)
        if source == "query":
            continue
        if source == "ebay_results":
            for key,value in items.items():
                for item in value:
                    item["source"] = source.replace("_results", "").replace("_", " ").title()
                    all_items.append(item)
        else:
            for item in items:
                item["source"] = source.replace("_results", "").replace("_", " ").title()
                all_items.append(item)
    
    return all_items

    # Insert into DB
    async with async_session() as db:
        inserted = 0
        for item in all_items:
            if not item.get("url") or not item.get("title"):
                continue

            result = await db.execute(select(Product).where(Product.url == item["url"]))
            if result.scalar():
                continue  # Skip duplicates

            db_item = Product(**{k: item.get(k) for k in Product.__table__.columns.keys() if k in item})
            db.add(db_item)
            inserted += 1

        await db.commit()

    return {
        "message": f"{inserted} new products inserted.",
        "total_scraped": len(all_items)
    }