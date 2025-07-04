# app/api/search_and_store.py
import boto3
from fastapi import APIRouter, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import async_session
from schemas.product_item import ProductItem
from sqlalchemy.future import select
from fuzzywuzzy import fuzz
import asyncio
from boto3.dynamodb.conditions import Attr
from utils.product_engine import ProductSearchEngine
from utils.logger import get_logger

router = APIRouter()
logger = get_logger()

dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
product_table = dynamodb.Table("ProductsTable")
category_tables = {
    "Shoes": dynamodb.Table("ShoesTable"),
    "Bags": dynamodb.Table("BagsTable"),
    "Accessories": dynamodb.Table("AccessoriesTable"),
    "Clothing": dynamodb.Table("ClothingTable"),
}

class ProductIdInput(BaseModel):
    product_id: Optional[str] = None
    filters: Optional[Dict[str, str]] = None  # {"min_price": "100", "max_price": "500", "location": "NY"}
    limit: Optional[int] = 10
    last_evaluated_key: Optional[Dict] = None
    group_by: Optional[str] = None  # e.g., "category", "brand" 


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
        filter_expr = None

        # Apply product_id filter if present
        if input_data.product_id:
            filter_expr = Attr("product_id").eq(input_data.product_id)

        # Apply additional filters
        filters = input_data.filters or {}
        for key, value in filters.items():
            if key == "min_price":
                expr = Attr("listing_price").gte(float(value))
            elif key == "max_price":
                expr = Attr("listing_price").lte(float(value))
            elif key == "from_date":
                expr = Attr("created_at").gte(value)
            elif key == "to_date":
                expr = Attr("created_at").lte(value)
            else:
                expr = Attr(key).eq(value)

            filter_expr = expr if not filter_expr else filter_expr & expr

        scan_args = {
            "Limit": input_data.limit,
        }

        if filter_expr:
            scan_args["FilterExpression"] = filter_expr

        if input_data.last_evaluated_key:
            scan_args["ExclusiveStartKey"] = input_data.last_evaluated_key

        response = product_table.scan(**scan_args)
        items = response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")

        if not items:
            return {"message": "No matching products found."}

        result = []
        for product in items:
            category = product.get("category")
            if not category or category not in category_tables:
                continue

            category_table = category_tables[category]
            meta_resp = category_table.get_item(Key={"url": product.get("url")})
            metadata = meta_resp.get("Item", {})

            result.append({
                "product": product,
                "category_metadata": metadata
            })

        # Optional grouping
        if input_data.group_by:
            grouped = {}
            for item in result:
                key = item["product"].get(input_data.group_by, "Unknown")
                grouped.setdefault(key, []).append(item)
            return {
                "grouped_results": grouped,
                "last_evaluated_key": last_key
            }

        return {
            "results": result,
            "last_evaluated_key": last_key
        }

    except Exception as e:
        logger.exception("Failed to fetch product by ID with filters")
        return {"error": str(e)}

def fetch_category_metadata(category: str, url: str):
    try:
        if category in category_tables:
            meta_resp = category_tables[category].get_item(Key={"url": url})
            return meta_resp.get("Item", {})
    except Exception as e:
        logger.error(f"Metadata fetch error: {e}")
    return {}

@router.post("/advanced_search/")
async def advanced_search(input_data: SearchInput):
    if not input_data.query and not input_data.image_url and not input_data.product_id:
        return {"error": "Provide at least one search input: query, image_url, or product_id"}

    engine = ProductSearchEngine()

    try:
        search_results = await engine.search(
            query=input_data.query,
            image_url=input_data.image_url,
            category=input_data.category,
            product_id=input_data.product_id
        )
    except Exception as e:
        logger.exception("Search engine failed")
        return {"error": str(e)}

    if not search_results:
        return {"message": "No results found"}

    enriched_results = []

    for source, items in search_results.items():
        if source == "query":
            continue
        if isinstance(items, dict):
            for subcat, products in items.items():
                for product in products:
                    url = product.get("url")
                    category = product.get("category", input_data.category)
                    metadata = fetch_category_metadata(category, url)
                    product["source"] = source.replace("_results", "").replace("_", " ").title()
                    product["category_metadata"] = metadata
                    product["relevance_score"] = fuzz.partial_ratio(input_data.query.lower(), product["title"].lower()) if input_data.query else 0
                    enriched_results.append(product)
        else:
            for product in items:
                url = product.get("url")
                category = product.get("category", input_data.category)
                metadata = fetch_category_metadata(category, url)
                product["source"] = source.replace("_results", "").replace("_", " ").title()
                product["category_metadata"] = metadata
                product["relevance_score"] = fuzz.partial_ratio(input_data.query.lower(), product["title"].lower()) if input_data.query else 0
                enriched_results.append(product)

    enriched_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    return enriched_results