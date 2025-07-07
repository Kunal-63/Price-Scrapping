# app/api/search_and_store.py
from datetime import datetime
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
from boto3.session import Session
from decimal import Decimal
from utils.gemini import GeminiRefiner

router = APIRouter()
logger = get_logger()

session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-2")
product_table = dynamodb.Table("ProductsTable")
category_tables = {
    "Shoes": dynamodb.Table("ShoesTable"),
    "Bags": dynamodb.Table("BagsTable"),
    "Accessories": dynamodb.Table("AccessoriesTable"),
    "Clothing": dynamodb.Table("ClothingTable"),
}

class ProductIdInput(BaseModel):
    product_id: Optional[str] = None
    query: Optional[str] = None
    filters: Optional[Dict[str, str]] = None
    limit: Optional[int] = 10
    last_evaluated_key: Optional[Dict] = None
    group_by: Optional[str] = None
    sort_by: Optional[str] = None  # New
    sort_order: Optional[str] = "asc"  # "asc" or "desc"


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

@router.post("/get_product/")
async def fetch_product_by_id(input_data: ProductIdInput):
    try:
        query = input_data.query.strip() if input_data.query else None
        filter_expr = None
        message = ""

        # --- Multi-field search expression ---
        def build_multi_field_filter(q: str):
            fields = ["title", "condition", "seller", "location", "category", "source"]
            expr = None
            for field in fields:
                cond = Attr(field).contains(q)
                expr = cond if expr is None else expr | cond
            return expr
        
        def safe_decimal(v):
            try:
                return Decimal(str(v).replace(",", "").replace("$", "").strip())
            except:
                return Decimal(0)

        # --- Base filter: product_id ---
        if input_data.product_id:
            filter_expr = Attr("product_id").eq(input_data.product_id)

        # --- Query filter ---
        if query:
            query_expr = build_multi_field_filter(query)
            if query_expr:
                filter_expr = query_expr if not filter_expr else filter_expr & query_expr

        # --- Additional filters ---
        filters = input_data.filters or {}
        for key, value in filters.items():
            if key == "min_price":
                expr = Attr("listing_price").gte(safe_decimal(value))
            elif key == "max_price":
                expr = Attr("listing_price").lte(safe_decimal(value))
            elif key == "from_date":
                expr = Attr("created_at").gte(value)
            elif key == "to_date":
                expr = Attr("created_at").lte(value)
            else:
                try:
                    value = Decimal(value)
                except:
                    pass
                expr = Attr(key).eq(value)
            filter_expr = expr if not filter_expr else filter_expr & expr

        # --- Scan args ---
        scan_args = {
            "Limit": input_data.limit,
        }
        if filter_expr:
            scan_args["FilterExpression"] = filter_expr
        if input_data.last_evaluated_key:
            scan_args["ExclusiveStartKey"] = input_data.last_evaluated_key

        # --- Fetch items ---
        response = product_table.scan(**scan_args)
        items = response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")

        # --- Fallback if no filtered results ---
        if not items:
            logger.info("No results matched filter — returning fallback products.")
            message = "No results matched filter"
            fallback_response = product_table.scan(Limit=input_data.limit)
            items = fallback_response.get("Items", [])
            last_key = fallback_response.get("LastEvaluatedKey")

        # --- Scoring ---
        def score_product(product: Dict, category: Dict, query: str) -> int:
            score = GeminiRefiner().score_product(product=product, category_metadata=category, query=query)
            return score if score is not None else 0
        # --- Enrich results ---
        result = []
        for product in items:
            category = product.get("category")
            if not category or category not in category_tables:
                continue
            metadata = fetch_category_metadata(category, product.get("url"))
            result.append({
                "product": product,
                "category_metadata": metadata,
                "score": score_product(product, metadata, query) if query else 100
            })

        # --- Relevance sorting ---
        if query:
            result.sort(key=lambda x: x["score"], reverse=True)

        # --- Sort by field ---
        if input_data.sort_by:
            sort_field = input_data.sort_by
            reverse_order = input_data.sort_order.lower() == "desc"
            try:
                result.sort(
                    key=lambda x: x["product"].get(sort_field) or 0,
                    reverse=reverse_order
                )
            except Exception as e:
                logger.warning(f"Sorting failed: {e}")

        # --- Grouping ---
        if input_data.group_by:
            grouped = {}
            for item in result:
                group_key = item["product"].get(input_data.group_by, "Unknown")

                # Special handling: convert ISO datetime to date
                if input_data.group_by == "created_at" and isinstance(group_key, str):
                    try:
                        group_key = datetime.fromisoformat(group_key.replace("Z", "")).date().isoformat()
                    except Exception as e:
                        logger.warning(f"Invalid created_at format: {group_key} - {e}")
                        group_key = "Invalid Date"

                grouped.setdefault(group_key, []).append(item)

            return {
                "grouped_results": grouped,
                "last_evaluated_key": last_key,
                "total_count": sum(len(items) for items in grouped.values()),
                "message": message or "Grouped results retrieved successfully"
            }

        # --- Final return ---
        return {
            "results": result,
            "last_evaluated_key": last_key,
            "total_count": len(result),
            "message": message or "Products retrieved successfully"
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
