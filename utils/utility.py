import random
import string

class Utility:
    @staticmethod
    def generate_random_session(length=25):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))