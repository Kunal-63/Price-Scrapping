import boto3
from boto3.session import Session
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict
import smtplib
from email.message import EmailMessage
import schedule
import time


# AWS session and tables
session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
product_table = dynamodb.Table("ProductsTable")
archive_table = dynamodb.Table("ArchiveTable")

# Email config
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = "587"
EMAIL_USER = "kunaladwani.work@gmail.com"
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_TO = "kunaladwani.work@gmail.com"

def send_email(subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.set_content(body)

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)

        print("📧 Email sent successfully.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

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
    grouped = defaultdict(lambda: defaultdict(list))
    for item in items:
        product_id = item.get("product_id")
        source = item.get("source", "unknown")
        listing_price = Decimal(str(item.get("listing_price", 0)))
        grouped[product_id][source].append(listing_price)

    results = []
    for product_id, source_data in grouped.items():
        archive_item = {
            "product_id": product_id,
            "archived_at": datetime.utcnow().isoformat()
        }
        for source, prices in source_data.items():
            if prices:
                avg_price = sum(prices) / len(prices)
                archive_item[f"{source}_avg_price"] = round(avg_price, 2)
        results.append(archive_item)

    return results

def archive_products_before(date_str: str):
    try:
        cutoff = datetime.fromisoformat(date_str)
    except Exception as e:
        print(f"❌ Invalid date format. Use YYYY-MM-DD. Error: {e}")
        return

    old_items = fetch_old_products(cutoff)
    grouped_data = group_and_average(old_items)

    if not grouped_data:
        print(f"ℹ️ No products found older than {date_str}.")
        return

    # Step 1: Merge and archive
    for item in grouped_data:
        product_id = item["product_id"]
        try:
            existing = archive_table.get_item(Key={"product_id": product_id}).get("Item")
        except Exception as e:
            print(f"⚠️ Error fetching archive for {product_id}: {e}")
            existing = None

        if existing:
            # Merge average prices using simple average (or weighted if you store count)
            for key, new_avg in item.items():
                if key.endswith("_avg_price") and key in existing:
                    combined_avg = round((Decimal(str(existing[key])) + Decimal(str(new_avg))) / 2, 2)
                    existing[key] = combined_avg
                elif key.endswith("_avg_price"):
                    existing[key] = new_avg
            existing["archived_at"] = datetime.utcnow().isoformat()
            final_item = existing
        else:
            final_item = item

        archive_table.put_item(Item=final_item)

    # Step 2: Delete originals
    with product_table.batch_writer() as delete_batch:
        for item in old_items:
            delete_batch.delete_item(Key={"product_id": item["product_id"]})

    summary = f"✅ Archived and deleted {len(grouped_data)} product entries older than {date_str}"
    print(summary)
    send_email("DynamoDB Archiving Report", summary)

def archive_daily():
    cutoff = datetime.utcnow() - timedelta(days=30)
    archive_products_before(cutoff.isoformat())

# # --- Uncomment below to run manually for a custom date ---
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