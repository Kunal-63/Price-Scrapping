import asyncio
import time
from fetch_scripts.fetch_ebay import EbayFetcher
from fetch_scripts.fetch_farfetch import FarFetchFetcher
from fetch_scripts.fetch_fashionphile import FashionphileFetcher
from fetch_scripts.fetch_google import GoogleFetcher
from fetch_scripts.fetch_rebag import RebagFetcher
from fetch_scripts.fetch_stockx import StockXFetcher

async def main():
    start_time = time.time()

    ebay = EbayFetcher(concurrency=20) # 40
    farfetch = FarFetchFetcher(concurrency=15) # 15
    fashionphile = FashionphileFetcher(concurrency=20) # 20
    google = GoogleFetcher(concurrency=50)
    rebag = RebagFetcher(concurrency=20) # 20
    stockx = StockXFetcher(concurrency=5) # 10

    await asyncio.gather(
        ebay.run(),
        farfetch.run(),
        fashionphile.run(),
        google.run(),
        rebag.run(),
        stockx.run()
    )

    end_time = time.time()
    duration = round(end_time - start_time, 2)
    print(f"✅ All fetchers completed in {duration} seconds.")

if __name__ == "__main__":
    asyncio.run(main())
