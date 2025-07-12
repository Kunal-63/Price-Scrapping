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
                {'AttributeName': key_name, 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': key_name, 'AttributeType': key_type}
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

def create_archive_table(dynamodb, table_name):
    try:
        existing_tables = dynamodb.meta.client.list_tables()['TableNames']
        if table_name in existing_tables:
            print(f"✅ Archive table already exists: {table_name}")
            return

        print(f"🛠️ Creating archive table: {table_name}")
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'product_id', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'product_id', 'AttributeType': 'S'}
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        table.meta.client.get_waiter('table_exists').wait(TableName=table_name)
        print(f"✅ Archive table created: {table_name}")
    except ClientError as e:
        print(f"❌ Error creating archive table {table_name}: {e}")

def create_product_table(dynamodb, table_name):
    try:
        existing_tables = dynamodb.meta.client.list_tables()['TableNames']
        if table_name in existing_tables:
            print(f"✅ Product table already exists: {table_name}")
            return

        print(f"🛠️ Creating product table: {table_name}")
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'url', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'url', 'AttributeType': 'S'},
                {'AttributeName': 'product_id', 'AttributeType': 'S'}
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'ProductIdIndex',
                    'KeySchema': [
                        {'AttributeName': 'product_id', 'KeyType': 'HASH'}
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        table.meta.client.get_waiter('table_exists').wait(TableName=table_name)
        print(f"✅ Product table created: {table_name}")
    except Exception as e:
        print(f"❌ Error creating product table {table_name}: {e}")

if __name__ == "__main__":
    session = Session()
    dynamodb = session.resource("dynamodb", region_name="us-east-1")

    # Create table with product_id as GSI
    # create_table_if_not_exists(dynamodb, "ProductsTable", "id")
    create_archive_table(dynamodb, "ArchiveProductsTable")

