# app/api/search_and_store.py
from fastapi import APIRouter, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import async_session
from schemas.product_item import ProductItem
from sqlalchemy.future import select
import asyncio

from utils.product_engine import ProductSearchEngine
from utils.logger import get_logger

router = APIRouter()
logger = get_logger()

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

@router.post("/advanced_search/")
async def search_advanced(input_data: SearchInput):
    if input_data.category and input_data.category not in ALLOWED_CATEGORIES:
        return {"error": f"Category '{input_data.category}' is not allowed."}

    filter_expr = None

    if input_data.query:
        filter_expr = Attr("title").contains(input_data.query)

    if input_data.min_price is not None:
        expr = Attr("listing_price").gte(input_data.min_price)
        filter_expr = filter_expr & expr if filter_expr else expr

    if input_data.max_price is not None:
        expr = Attr("listing_price").lte(input_data.max_price)
        filter_expr = filter_expr & expr if filter_expr else expr

    if input_data.location:
        expr = Attr("location").eq(input_data.location)
        filter_expr = filter_expr & expr if filter_expr else expr

    if input_data.condition:
        expr = Attr("condition").eq(input_data.condition)
        filter_expr = filter_expr & expr if filter_expr else expr

    if input_data.seller:
        expr = Attr("seller").eq(input_data.seller)
        filter_expr = filter_expr & expr if filter_expr else expr

    if input_data.product_id:
        expr = Attr("product_id").eq(input_data.product_id)
        filter_expr = filter_expr & expr if filter_expr else expr

    # Perform scan
    scan_kwargs = {"FilterExpression": filter_expr} if filter_expr else {}
    response = product_table.scan(**scan_kwargs)
    results = response.get("Items", [])

    # Category-specific filters
    if input_data.category and (input_data.color or input_data.brand):
        category_table = category_tables.get(input_data.category)
        cat_filter_expr = None

        if input_data.color:
            expr = Attr("color").eq(input_data.color)
            cat_filter_expr = expr

        if input_data.brand:
            expr = Attr("brand").eq(input_data.brand)
            cat_filter_expr = cat_filter_expr & expr if cat_filter_expr else expr

        category_response = category_table.scan(
            FilterExpression=cat_filter_expr
        )
        valid_product_ids = {item["id"] for item in category_response.get("Items", [])}

        # Filter main results using category product IDs
        results = [r for r in results if r["product_id"] in valid_product_ids]

    return results if results else {"message": "No results found"}