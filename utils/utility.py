import uuid

class Utility:
    @staticmethod
    def generate_random_session():
        return str(uuid.uuid4())
