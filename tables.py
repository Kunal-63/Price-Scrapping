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
                },
                {
                    'AttributeName': 'product_id',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'ProductIdIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'product_id',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
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
        print(f"✅ Table created: {table_name}")

    except ClientError as e:
        print(f"❌ Error creating table {table_name}: {e}")

def delete_all_items(dynamodb, table_name, key_name='url'):
    table = dynamodb.Table(table_name)

    print(f"⚠️ Deleting all items from table: {table_name}")
    scan_kwargs = {}
    deleted = 0

    while True:
        response = table.scan(**scan_kwargs)
        items = response.get('Items', [])
        if not items:
            break

        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={key_name: item[key_name]})
                deleted += 1

        # pagination
        if 'LastEvaluatedKey' in response:
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        else:
            break

    print(f"✅ Deleted {deleted} items from {table_name}")

if __name__ == "__main__":
    session = Session()
    dynamodb = session.resource("dynamodb", region_name="us-east-1")

    # Create table with product_id as GSI
    create_table_if_not_exists(dynamodb, "ProductsTable", "url")
