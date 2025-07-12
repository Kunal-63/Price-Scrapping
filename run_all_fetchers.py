# run_all_fetchers.py
import asyncio
from fetch_scripts.fetch_ebay import EbayFetcher

async def main():
    ebay = EbayFetcher(concurrency=5)
    await ebay.run()

if __name__ == "__main__":
    asyncio.run(main())
