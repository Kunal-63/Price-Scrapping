from datetime import datetime, timedelta
from boto3.session import Session
from config.aws import AwsConfig

class GlobalLockManager:
    def __init__(self, task_id="global_scraper_lock", ttl_minutes=30):
        session = Session()
        self.lock_table = session.resource("dynamodb", region_name=AwsConfig.AWS_REGION).Table(AwsConfig.FETCHERS_LOCK_TABLE)
        self.task_id = task_id
        self.ttl_minutes = ttl_minutes

    def is_locked(self) -> bool:
        now = datetime.utcnow().isoformat()
        response = self.lock_table.scan(
            FilterExpression="#s = :running AND expires_at > :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":running": "RUNNING",
                ":now": now
            }
        )
        return len(response.get("Items", [])) > 0

    def acquire(self, script_name: str):
        now = datetime.utcnow()
        expires = now + timedelta(minutes=self.ttl_minutes)
        self.lock_table.put_item(
            Item={
                "task_id": self.task_id,
                "script_name": script_name,
                "locked_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "status": "RUNNING"
            }
        )
        print(f"🔒 Acquired global lock for {script_name}")

    def release(self, time_taken: float = None):
        update_expr = "SET #s = :done"
        expr_attr_vals = {":done": "COMPLETED"}
        expr_attr_names = {"#s": "status"}

        if time_taken is not None:
            update_expr += ", time_taken = :tt"
            expr_attr_vals[":tt"] = round(time_taken, 2)

        self.lock_table.update_item(
            Key={"task_id": self.task_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_vals
        )
        print("🔓 Released global lock")
