# app/api/search_and_store.py
import boto3
from fastapi import APIRouter, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import async_session
from schemas.product_item import ProductItem
from sqlalchemy.future import select
import asyncio
from boto3.dynamodb.conditions import Attr
from utils.product_engine import ProductSearchEngine
from utils.logger import get_logger

router = APIRouter()
logger = get_logger()

dynamodb = boto3.resource("dynamodb")
product_table = dynamodb.Table("ProductsTable")
category_tables = {
    "Shoes": dynamodb.Table("ShoesTable"),
    "Bags": dynamodb.Table("BagsTable"),
    "Accessories": dynamodb.Table("AccessoriesTable"),
    "Clothing": dynamodb.Table("ClothingTable"),
}

class ProductIdInput(BaseModel):
    product_id: str


class SearchInput(BaseModel):
    query: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[HttpUrl] = None
    product_id: Optional[str] = None


@router.post("/insert/")
async def search_and_store(input_data: SearchInput):
    if not input_data.query and not input_data.image_url:
        return {"error": "Provide either 'query' or 'image_url'."}

    engine = ProductSearchEngine()
    try:
        combined = await engine.search(query=input_data.query, image_url=input_data.image_url, category=input_data.category, product_id=input_data.product_id)
    except Exception as e:
        logger.exception("Search failed")
        return {"error": str(e)}

    if not combined:
        return {"message": "No results found"}
    
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

@router.post("/product_by_id/")
async def fetch_product_by_id(input_data: ProductIdInput):
    try:
        response = product_table.scan(
            FilterExpression=Attr("product_id").eq(input_data.product_id)
        )
        items = response.get("Items", [])

        if not items:
            return {"message": "Product not found."}

        product = items[0]
        category = product.get("category")

        if not category or category not in category_tables:
            return {"error": f"Invalid or unknown category: {category}"}

        category_table = category_tables[category]
        meta_resp = category_table.get_item(Key={"url": product.get("url")})
        metadata = meta_resp.get("Item", {})

        return {
            "product": product,
            "category_metadata": metadata
        }

    except Exception as e:
        logger.exception("Failed to fetch product by ID")
        return {"error": str(e)}