import boto3
from boto3.session import Session
from botocore.exceptions import ClientError

def create_table_if_not_exists(dynamodb, table_name, key_name, key_type='S'):
    try:
        existing_tables = dynamodb.meta.client.list_tables()['TableNames']
        if table_name in existing_tables:
            print(f"✅ Table already exists: {table_name}")
            return

        print(f"🛠️ Creating table: {table_name}")
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': key_name,
                    'KeyType': 'HASH'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': key_name,
                    'AttributeType': key_type
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        table.meta.client.get_waiter('table_exists').wait(TableName=table_name)
        print(f"✅ Table created: {table_name}")
    except ClientError as e:
        print(f"❌ Error creating table {table_name}: {e}")

if __name__ == "__main__":
    # Use AWS SSO profile
    session = Session(profile_name="auricore-dev-kunal")
    dynamodb = session.resource("dynamodb", region_name="us-east-2")

    # Product and metadata tables
    create_table_if_not_exists(dynamodb, "ProductsTable", "url")
    create_table_if_not_exists(dynamodb, "ShoesTable", "product_id")
    create_table_if_not_exists(dynamodb, "BagsTable", "product_id")
    create_table_if_not_exists(dynamodb, "AccessoriesTable", "product_id")
    create_table_if_not_exists(dynamodb, "ClothingTable", "product_id")
