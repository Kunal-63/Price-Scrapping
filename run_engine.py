import asyncio
import boto3
from boto3.dynamodb.conditions import Key, Attr

from utils.product_engine import ProductSearchEngine

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table_name = "LuxuryProductsTable"
table = dynamodb.Table(table_name)

category_mapping = {
    "shoes": "Shoes",
    "bags": "Bags",
    "accessories": "Accessories",
    "clothing": "Clothing",
    "beauty": "Beauty",
    "jewelry": "Jewelry"
}

async def process_all_items():
    search_engine = ProductSearchEngine()
    last_evaluated_key = None
    batch_number = 1

    while True:
        scan_kwargs = {
            "Limit": 25
        }
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        print(f"\n🔄 Batch {batch_number}: Retrieved {len(items)} items")

        # Run ProductSearchEngine for each item
        for item in items:
            query = item.get("product_name")
            category = item.get("category")
            modified_category = category_mapping.get(category.lower(), "Unknown")
            product_id = item.get("product_id")

            result = await search_engine.search(
                query=query,
                category=modified_category,
                product_id=product_id
            )

            # Do something with `result`, e.g. log, save to DB, write to file, etc.
            print(f"✅ Search complete for: {query}")

        batch_number += 1

        # Check for more items
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

# Entry point
if __name__ == "__main__":
    asyncio.run(process_all_items())
