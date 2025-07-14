import asyncio
import time
from fetch_scripts.fetch_ebay import EbayFetcher
from fetch_scripts.fetch_farfetch import FarFetchFetcher
from fetch_scripts.fetch_fashionphile import FashionphileFetcher
from fetch_scripts.fetch_google import GoogleFetcher
from fetch_scripts.fetch_rebag import RebagFetcher
from fetch_scripts.fetch_stockx import StockXFetcher
from utils.global_lock import GlobalLockManager

async def main():
    lock = GlobalLockManager()

    if lock.is_locked():
        print("⏭️ Skipping run — another scraper is already running.")
        return

    lock.acquire("run_all_fetchers")
    start_time = time.time()

    try:
        ebay = EbayFetcher(concurrency=20)
        farfetch = FarFetchFetcher(concurrency=15)
        fashionphile = FashionphileFetcher(concurrency=15)
        google = GoogleFetcher(concurrency=20)
        rebag = RebagFetcher(concurrency=15)
        stockx = StockXFetcher(concurrency=5)

        await asyncio.gather(
            ebay.run(),
            farfetch.run(),
            fashionphile.run(),
            google.run(),
            rebag.run(),
            stockx.run()
        )

        duration = round(time.time() - start_time, 2)
        print(f"✅ All fetchers completed in {duration} seconds.")
        lock.release(time_taken=duration)

    except Exception as e:
        print(f"❌ Error during run_all_fetchers: {e}")
        lock.release()

if __name__ == "__main__":
    asyncio.run(main())