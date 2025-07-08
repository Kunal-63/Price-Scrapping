import asyncio
import boto3
from boto3.dynamodb.conditions import Key, Attr
from boto3.session import Session
from utils.product_engine import ProductSearchEngine


session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
table_name = "FinalProductsTable"
table = dynamodb.Table(table_name)

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
            query = item.get("canonical_name")
            category = item.get("category")
            product_id = item.get("product_id")

            imageurls = item.get('all_image_urls', [])
            image = None
            if imageurls:
                image = imageurls[0]

            result = await search_engine.search(
                query=query,
                image_url=image,
                category=category,
                product_id=product_id
            )

            print(f"✅ Search complete for: {query}")

        batch_number += 1

        # Check for more items
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

# Entry point
if __name__ == "__main__":
    asyncio.run(process_all_items())
