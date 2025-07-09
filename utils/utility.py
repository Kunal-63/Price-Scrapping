import re
import uuid

class Utility:
    @staticmethod
    def generate_random_session():
        return str(uuid.uuid4())
    
    @staticmethod
    def extract_location_from_title(self, title: str) -> str:
        if not title:
            return "US"

        title_clean = title.lower()

        patterns = [
            (r'\b(u\.?s\.?a?|united states)\b', 'US'),
            (r'\b(u\.?k\.?|united kingdom)\b', 'UK'),
            (r'\b(eu|european union|europe)\b', 'EU'),
            (r'\bcanada\b', 'Canada'),
            (r'\b(australia|au)\b', 'Australia'),
            (r'\b(japan|jp)\b', 'Japan'),
            (r'\bital(y|ia)\b', 'Italy'),
            (r'\bfrance\b', 'France'),
            (r'\bgermany\b', 'Germany'),
        ]

        compact_patterns = [
            (r'\b(us[\s\-]?\d+|\d+[\s\-]?us)\b', 'US'),
            (r'\b(uk[\s\-]?\d+|\d+[\s\-]?uk)\b', 'UK'),
            (r'\b(eu[\s\-]?\d+|\d+[\s\-]?eu)\b', 'EU'),
        ]

        for pattern, location in compact_patterns + patterns:
            if re.search(pattern, title_clean):
                return location

        return "US"
    
    @staticmethod
    def refine_us_size_string(size: str) -> str:
        if not size:
            return "Not present"

        size = size.lower()

        size = re.sub(r"\b(men'?s|women'?s|kids|youth|unisex|mens|womens|boys|girls)\b", '', size)

        size = re.sub(r'\b(eu|uk|it|fr|jp|cn|asia)\b', '', size)

        size = re.sub(r'[()\[\],]', ' ', size)
        size = re.sub(r'\s+', ' ', size).strip()

        return size.upper() if size else "Not present"