import asyncio
import time
import boto3
from boto3.session import Session
from utils.product_engine import ProductSearchEngine
from config.aws import AwsConfig
from utils.global_lock import GlobalLockManager

session = Session()
dynamodb = session.resource("dynamodb", region_name=AwsConfig.AWS_REGION)
table = dynamodb.Table(AwsConfig.FINAL_PRODUCT_TABLE)

CONCURRENCY = 6

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
    lock = GlobalLockManager()

    if lock.is_locked():
        print("⏭️ Skipping run — another scraper is already running.")
        return

    lock.acquire("run_engine")
    start_all = time.time()

    try:
        search_engine = ProductSearchEngine()
        semaphore = asyncio.Semaphore(CONCURRENCY)

        response = table.scan()
        items = response.get("Items", [])
        print(f"🔍 Retrieved {len(items)} items for processing")

        half = len(items) // 2
        first_batch = items[:half]
        second_batch = items[half:]

        tasks = [asyncio.create_task(process_item(search_engine, item, semaphore)) for item in first_batch]

        await asyncio.sleep(10)
        print("⏳ Launching second batch after 10 seconds...")
        tasks += [asyncio.create_task(process_item(search_engine, item, semaphore)) for item in second_batch]

        await asyncio.gather(*tasks)

        total_time = time.time() - start_all
        print(f"✅ All items processed in {total_time:.2f} seconds")
        lock.release(time_taken=total_time)

    except Exception as e:
        print(f"❌ Error during engine run: {e}")
        lock.release()

if __name__ == "__main__":
    asyncio.run(process_top_items())