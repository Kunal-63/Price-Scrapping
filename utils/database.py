from datetime import datetime
import boto3
from boto3.session import Session

session = Session()
dynamodb = session.resource("dynamodb", region_name="us-east-1")
product_table = dynamodb.Table("ProductsTable")

def is_duplicate_today(url: str) -> bool:
    today_str = datetime.utcnow().date().isoformat()

    response = product_table.query(
        IndexName="UrlCreatedAtIndex",
        KeyConditionExpression="#u = :url_val AND begins_with(created_at, :date_prefix)",
        ExpressionAttributeNames={
            "#u": "url"
        },
        ExpressionAttributeValues={
            ":url_val": url,
            ":date_prefix": today_str
        }
    )

    return len(response.get("Items", [])) > 0