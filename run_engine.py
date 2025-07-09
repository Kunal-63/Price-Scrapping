import asyncio
import time
import boto3
from boto3.session import Session
from utils.product_engine import ProductSearchEngine

session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table("FinalProductsTable")

CONCURRENCY = 3  # max concurrent product searches
BATCH_SIZE = 25   # number of items to scan per DynamoDB read


async def process_item(search_engine, item, semaphore):
    async with semaphore:
        start = time.time()
        try:
            query = item.get("canonical_name")
            category = item.get("category")
            product_id = item.get("product_id")
            imageurls = item.get("all_image_urls", [])
            image = imageurls[0] if imageurls else None
            result = await search_engine.search(
                query=query,
                image_url=image,
                category=category,
                product_id=product_id
            )
            print(f"✅ Search complete for: {query}")
            duration = time.time() - start
            print(f"✅ {query} done in {duration:.2f}s")
            return result
        except Exception as e:
            print(f"❌ Error processing item {item.get('canonical_name')}: {e}")
            return None


async def process_all_items():
    search_engine = ProductSearchEngine()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    last_evaluated_key = None
    batch_number = 1

    while True:
        scan_kwargs = {"Limit": BATCH_SIZE}
        if last_evaluated_key:
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        print(f"\n🔄 Batch {batch_number}: Retrieved {len(items)} items")

        tasks = [asyncio.create_task(process_item(search_engine, item, semaphore)) for item in items]
        await asyncio.gather(*tasks)

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

        batch_number += 1


if __name__ == "__main__":
    asyncio.run(process_all_items())
