import random
import string
import time

class Utility:
    @staticmethod
    def generate_random_session(length=25):
        time.sleep(1)
        session_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
        timestamp_ms = int(time.time() * 1000)
        return session_id + str(timestamp_ms)
