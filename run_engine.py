import asyncio
import boto3
from boto3.session import Session
from utils.product_engine import ProductSearchEngine

session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
table_name = "FinalProductsTable"
table = dynamodb.Table(table_name)

CONCURRENCY = 5


async def process_item(search_engine, item, semaphore):
    async with semaphore:
        query = item.get("canonical_name")
        category = item.get("category")
        product_id = item.get("product_id")

        imageurls = item.get('all_image_urls', [])
        image = imageurls[0] if imageurls else None

        result = await search_engine.search(
            query=query,
            image_url=image,
            category=category,
            product_id=product_id
        )

        print(f"✅ Search complete for: {query}")
        return result


async def process_all_items():
    search_engine = ProductSearchEngine()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    last_evaluated_key = None
    batch_number = 1

    while True:
        scan_kwargs = {"Limit": 25}
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        print(f"\n🔄 Batch {batch_number}: Retrieved {len(items)} items")

        tasks = [process_item(search_engine, item, semaphore) for item in items]
        await asyncio.gather(*tasks)

        batch_number += 1
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break


# Entry point
if __name__ == "__main__":
    asyncio.run(process_all_items())
