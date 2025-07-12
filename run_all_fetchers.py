# run_all_fetchers.py
import asyncio
import time
from fetch_scripts.fetch_ebay import EbayFetcher

async def main():
    start_time = time.time()

    ebay = EbayFetcher(concurrency=10)
    await ebay.run()

    end_time = time.time()
    duration = round(end_time - start_time, 2)
    print(f"✅ All fetchers completed in {duration} seconds.")

if __name__ == "__main__":
    asyncio.run(main())
