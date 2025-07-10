import asyncio
import time
import boto3
from boto3.session import Session
from utils.product_engine import ProductSearchEngine

session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table("FinalProductsTable")

CONCURRENCY = 5

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
            print(f"⏱️ Time taken: {time.time() - start:.2f}s")
            return result
        except Exception as e:
            print(f"❌ Error processing {item.get('canonical_name')}: {e}")
            return None

async def process_top_items():
    search_engine = ProductSearchEngine()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    response = table.scan(Limit=100)
    items = response.get("Items", [])
    print(f"🔍 Retrieved {len(items)} items for processing")

    first_batch = items[:2]
    second_batch = items[2:]

    start_all = time.time()

    tasks = [asyncio.create_task(process_item(search_engine, item, semaphore)) for item in first_batch]

    await asyncio.sleep(10)
    print("⏳ Launching second batch after 10 seconds...")
    tasks += [asyncio.create_task(process_item(search_engine, item, semaphore)) for item in second_batch]

    await asyncio.gather(*tasks)

    total_time = time.time() - start_all
    print(f"✅ All items processed in {total_time:.2f} seconds")

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(process_top_items())
