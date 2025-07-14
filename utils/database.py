from datetime import datetime
import boto3
from boto3.session import Session

from config.aws import AwsConfig

session = Session()
dynamodb = session.resource("dynamodb", region_name=AwsConfig.AWS_REGION)
product_table = dynamodb.Table(AwsConfig.PRODUCT_TABLE)

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

def is_duplicate_by_id(item_id: str) -> bool:
    response = product_table.get_item(Key={"id": item_id})
    return "Item" in response