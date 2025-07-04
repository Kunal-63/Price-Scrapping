import random
import string
import time

class Utility:
    @staticmethod
    def generate_random_session(length=25):
        time.sleep(2)
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length)) + str(int(time.time()))