import boto3
from boto3.session import Session
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict
import schedule
import time
from utils.email import send_email

# AWS session and tables
session = Session(profile_name='auricore-dev-kunal')
dynamodb = session.resource("dynamodb", region_name="us-east-1")
product_table = dynamodb.Table("ProductsTable")
archive_table = dynamodb.Table("ArchiveTable")

def is_older_than(iso_date: str, cutoff: datetime) -> bool:
    try:
        created_date = datetime.fromisoformat(iso_date.replace('Z', ''))
        return created_date < cutoff
    except Exception:
        return False

def fetch_old_products(cutoff: datetime):
    response = product_table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = product_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [item for item in items if is_older_than(item.get("created_at", ""), cutoff)]

def group_and_average(items):
    grouped = defaultdict(lambda: defaultdict(list))  # {product_id: {source: [prices]}}
    for item in items:
        product_id = item.get("product_id")
        source = item.get("source", "unknown")
        listing_price = Decimal(str(item.get("listing_price", 0)))
        grouped[product_id][source].append(listing_price)

    results = []
    for product_id, source_data in grouped.items():
        avg_prices = {}
        for source, prices in source_data.items():
            if prices:
                avg = sum(prices) / len(prices)
                avg_prices[source] = round(avg, 2)
        results.append({
            "product_id": product_id,
            "avg_prices": avg_prices
        })
    return results

def archive_products_before(date_str: str):
    try:
        cutoff = datetime.fromisoformat(date_str)
    except Exception as e:
        print(f"❌ Invalid date format. Use YYYY-MM-DD. Error: {e}")
        return

    archive_date_str = cutoff.date().isoformat()
    old_items = fetch_old_products(cutoff)
    grouped_data = group_and_average(old_items)

    if not grouped_data:
        print(f"ℹ️ No products found older than {date_str}.")
        return

    for item in grouped_data:
        product_id = item["product_id"]
        try:
            archive_table.update_item(
                Key={"product_id": product_id},
                UpdateExpression=f"SET #d = :data",
                ExpressionAttributeNames={"#d": archive_date_str},
                ExpressionAttributeValues={":data": item["avg_prices"]}
            )
        except Exception as e:
            print(f"⚠️ Error archiving for {product_id}: {e}")

    # Delete originals
    with product_table.batch_writer() as delete_batch:
        for item in old_items:
            delete_batch.delete_item(Key={"product_id": item["product_id"]})

    summary = f"✅ Archived {len(grouped_data)} product entries to column '{archive_date_str}' and deleted them from ProductsTable."
    print(summary)
    send_email("DynamoDB Archiving Report", summary)

def archive_daily():
    cutoff = datetime.utcnow() - timedelta(days=30)
    archive_products_before(cutoff.isoformat())

# --- Uncomment below to run manually for a custom date ---
# custom_date = "2025-07-12"  # 🔁 Change this date as needed in YYYY-MM-DD format
# archive_products_before(custom_date)
# exit()  # 🛑 Prevents the scheduler from running if you're just doing a one-time custom archive
# ----------------------------------------------------------

# Schedule: Run daily at 02:00 AM
schedule.every().day.at("02:00").do(archive_daily)

print("⏰ Scheduler started. Waiting for 2:00 AM daily run...")
while True:
    schedule.run_pending()
    time.sleep(60)
