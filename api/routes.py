# app/api/search_and_store.py
from datetime import datetime
import time
import boto3
from fastapi import APIRouter, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.product_item import ProductItem
from sqlalchemy.future import select
from fuzzywuzzy import fuzz
import asyncio
from boto3.dynamodb.conditions import Attr, Key
from utils.product_engine import ProductSearchEngine
from utils.logger import get_logger
from boto3.session import Session
from decimal import Decimal
from utils.antropic import AnthropicRefiner
from models.product_item import ProductModel
from collections import defaultdict, Counter
from schemas.productId_input import ProductIdInput
from schemas.search_input import SearchInput
from config.aws import AwsConfig
from utils.global_lock import GlobalLockManager


router = APIRouter()
logger = get_logger(__name__)

session = Session()
dynamodb = session.resource("dynamodb", region_name=AwsConfig.AWS_REGION)
product_table = dynamodb.Table(AwsConfig.PRODUCT_TABLE)
final_table = dynamodb.Table(AwsConfig.FINAL_PRODUCT_TABLE)

@router.post("/insert/")
async def search_and_store(input_data: SearchInput):
    if not input_data.query and not input_data.image_url:
        return {"error": "Provide either 'query' or 'image_url'."}

    lock = GlobalLockManager()

    if lock.is_locked():
        return {"error": "Scraper is currently running. Try again later."}

    lock.acquire("insert_api")
    engine = ProductSearchEngine()
    start_time = time.perf_counter()

    try:
        combined = await engine.search(
            query=input_data.query,
            image_url=input_data.image_url,
            category=input_data.category,
            product_id=input_data.product_id,
        )
    except Exception as e:
        logger.exception("Search failed")
        lock.release()
        return {"error": str(e)}

    if not combined:
        lock.release()
        return {"message": "No results found"}

    all_items = []
    for source, items in combined.items():
        print(source, items)
        if source == "query":
            continue
        if source == "ebay_results":
            for key, value in items.items():
                for item in value:
                    item["source"] = source.replace("_results", "").replace("_", " ").title()
                    all_items.append(item)
        else:
            for item in items:
                item["source"] = source.replace("_results", "").replace("_", " ").title()
                all_items.append(item)

    end_time = time.perf_counter()
    duration = round(end_time - start_time, 2)
    lock.release(time_taken=duration)

    return {
        "time_taken_seconds": duration,
        "result_count": len(all_items),
        "results": all_items
    }

@router.post("/get_product/")
async def fetch_product_by_id(input_data: ProductIdInput):
    try:
        query = input_data.query.strip() if input_data.query else None
        message = ""
        items = []
        last_key = None

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

        # --- Use GSI query if product_id is specified ---
        if input_data.product_id:
            logger.info(f"Using ProductIdIndex query for product_id: {input_data.product_id}")
            response = product_table.query(
                IndexName="ProductIdIndex",
                KeyConditionExpression=Key("product_id").eq(input_data.product_id),
                Limit=input_data.limit
            )
            items = response.get("Items", [])
            last_key = response.get("LastEvaluatedKey")

            # Ignore 'query' to avoid mixing relevance filters
            if query:
                logger.warning("Ignoring 'query' parameter when 'product_id' is provided to ensure accuracy.")

            # Apply extra filters in Python
            filters = input_data.filters or {}
            def passes_filters(item):
                for key, value in filters.items():
                    try:
                        if key == "min_price" and Decimal(item.get("listing_price", 0)) < safe_decimal(value):
                            return False
                        if key == "max_price" and Decimal(item.get("listing_price", 0)) > safe_decimal(value):
                            return False
                        if key == "from_date" and item.get("created_at", "") < value:
                            return False
                        if key == "to_date" and item.get("created_at", "") > value:
                            return False
                        if key not in ["min_price", "max_price", "from_date", "to_date"]:
                            if str(item.get(key)) != str(value):
                                return False
                    except:
                        continue
                return True

            items = list(filter(passes_filters, items))

        else:
            # --- Fuzzy search using scan ---
            filter_expr = None
            if query:
                filter_expr = build_multi_field_filter(query)

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
                logger.info("No results matched filter — returning fallback products.")
                message = "No results matched filter"
                fallback_response = product_table.scan(Limit=input_data.limit)
                items = fallback_response.get("Items", [])
                last_key = fallback_response.get("LastEvaluatedKey")

        # --- Scoring and conversion ---
        results = []
        refiner = AnthropicRefiner()
        for item in items:
            product_model = ProductModel(item, item.get("category", "unknown"))
            score = refiner.score_product(product_model.to_dict(), {}, query) if query else 100
            results.append({
                "product": product_model.to_dict(),
                "score": score
            })

        # --- Relevance sort ---
        if query:
            results.sort(key=lambda x: x["score"], reverse=True)

        # --- Optional sort ---
        if input_data.sort_by:
            try:
                reverse_order = input_data.sort_order.lower() == "desc"
                results.sort(
                    key=lambda x: x["product"].get(input_data.sort_by) or 0,
                    reverse=reverse_order
                )
            except Exception as e:
                logger.warning(f"Sorting failed: {e}")

        # --- Grouping ---
        if input_data.group_by:
            grouped = {}
            for item in results:
                group_key = item["product"].get(input_data.group_by, "Unknown")
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
                "total_count": sum(len(g) for g in grouped.values()),
                "message": message or "Grouped results retrieved successfully"
            }

        # --- Final response ---
        return {
            "results": results,
            "last_evaluated_key": last_key,
            "total_count": len(results),
            "message": message or "Products retrieved successfully"
        }

    except Exception as e:
        logger.exception("Failed to fetch product by ID with filters")
        return {"error": str(e)}

@router.get("/count_by_source/")
async def count_products_by_source(product_id: Optional[str] = None):
    try:
        all_items = []

        if product_id:
            response = product_table.query(
                IndexName="ProductIdIndex",
                KeyConditionExpression=Key("product_id").eq(product_id)
            )
            all_items.extend(response.get("Items", []))

            while "LastEvaluatedKey" in response:
                response = product_table.query(
                    IndexName="ProductIdIndex",
                    KeyConditionExpression=Key("product_id").eq(product_id),
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                all_items.extend(response.get("Items", []))
        else:
            response = product_table.scan()
            all_items.extend(response.get("Items", []))

            while "LastEvaluatedKey" in response:
                response = product_table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                all_items.extend(response.get("Items", []))

        product_source_counts = defaultdict(Counter)

        for item in all_items:
            pid = item.get("product_id", "Unknown")
            source = item.get("source", "Unknown")
            product_source_counts[pid][source] += 1

        result = {pid: dict(source_counts) for pid, source_counts in product_source_counts.items()}

        return {
            "product_source_counts": result,
            "total_products": len(result),
            "filtered_by_product_id": bool(product_id)
        }

    except Exception as e:
        logger.exception("Error counting products by source")
        return {"error": str(e)}
@router.get("/products_by_condition/")
async def products_by_condition(condition: str = Query(default=None)):
    try:
        all_items = []

        if condition:
            filter_expr = Attr("condition").eq(condition)
            response = product_table.scan(FilterExpression=filter_expr)
        else:
            response = product_table.scan()

        all_items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            if condition:
                response = product_table.scan(
                    FilterExpression=filter_expr,
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
            else:
                response = product_table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
            all_items.extend(response.get("Items", []))

        if condition:
            return {
                "condition": condition,
                "count": len(all_items),
                "products": all_items
            }

        grouped = defaultdict(lambda: defaultdict(int))
        for item in all_items:
            cond = item.get("condition", "Unknown")
            src = item.get("source", "Unknown")
            grouped[cond][src] += 1

        summary = {
            cond: dict(srcs) for cond, srcs in grouped.items()
        }

        return {
            "total_products": len(all_items),
            "grouped_by_condition_and_source": summary
        }

    except Exception as e:
        return {"error": str(e)}
    
@router.get("/missing-products", response_model=List[str])
async def get_missing_products(limit: int = Query(50, gt=0, le=500)):
    def check_if_product_exists(product_id: str) -> bool:
        resp = product_table.query(
            IndexName='ProductIdIndex',
            KeyConditionExpression=Key('product_id').eq(product_id),
            Limit=1
        )
        return bool(resp.get('Items'))

    response = final_table.scan(ProjectionExpression="product_id")
    all_product_ids = [item['product_id'] for item in response.get('Items', [])]

    while 'LastEvaluatedKey' in response and len(all_product_ids) < limit * 3:
        response = final_table.scan(
            ProjectionExpression="product_id",
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        all_product_ids += [item['product_id'] for item in response.get('Items', [])]

    missing = []

    async def check(pid):
        if not await asyncio.to_thread(check_if_product_exists, pid):
            missing.append(pid)

    await asyncio.gather(*[check(pid) for pid in all_product_ids])
    return missing[:limit]