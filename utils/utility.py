import re
import uuid
import numpy as np
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer, util

_sbert = SentenceTransformer("all-MiniLM-L6-v2")

def generate_sample_sizes():
    us_sizes = [f"{i}" for i in range(4, 15)] + [f"{i}.5" for i in range(4, 14)]
    us_sizes = us_sizes + [f"us {s}" for s in us_sizes]

    uk_sizes = [f"uk {i}" for i in range(4, 13)]
    eu_sizes = [f"eu {i}" for i in range(35, 49)]
    inch_sizes = [f"{i} inch" for i in range(6, 14)] + [f"{i} inches" for i in range(6, 14)]
    cm_sizes = [f"{i} cm" for i in range(22, 32)]
    mm_sizes = [f"{i} mm" for i in range(220, 320, 5)]

    text_sizes = ["xs", "s", "m", "l", "xl", "xxl", "xxxl", "extra small", "small", "medium", "large", "extra large"]

    return set(us_sizes + uk_sizes + eu_sizes + inch_sizes + cm_sizes + mm_sizes + text_sizes)

SAMPLE_SIZES = generate_sample_sizes()


class Utility:
    @staticmethod
    def generate_random_session():
        return str(uuid.uuid4())
    
    @staticmethod
    def extract_location_from_title(title: str) -> str:
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
    
    @staticmethod
    def extract_size_from_title(title: str) -> str:
        if not title:
            return "Not present"

        title = title.lower()

        size_patterns = [
            r'\b(us|uk|eu|jp|it|fr)?[\s\-:]?(size[\s\-:]?)?(\d{1,2}(\.\d)?)(?=\b|[^a-z0-9])\b',
            r'\b(xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl|extra small|small|medium|large|extra large)\b',
            r'\b(\d{1,2}(\.\d)?)[ ]?(us|uk|eu|jp|it|fr)\b',
            r'\bw(\d{1,2}) ?l(\d{1,2})\b',
            r'\b\d{2,3} ?(cm|mm|inch|inches)\b'
        ]

        possible_sizes = []
        for pattern in size_patterns:
            for match in re.finditer(pattern, title):
                candidate = match.group().strip()
                cleaned = re.sub(r'\bsize\b[\s\-:]*', '', candidate, flags=re.IGNORECASE).strip()
                possible_sizes.append((cleaned, match.start()))

        if not possible_sizes:
            return "Not present"

        for size_candidate, start in possible_sizes:
            context = title[max(0, start - 15):start + 15]
            score = fuzz.partial_ratio(context, f"size {size_candidate}")
            embedding_score = util.cos_sim(
                _sbert.encode([context])[0],
                _sbert.encode(["shoe size"])[0]
            ).item()

            normalized = size_candidate.lower().strip()

            # Validate against sample sizes
            if (
                normalized in SAMPLE_SIZES
                or any(fuzz.ratio(normalized, sample) > 90 for sample in SAMPLE_SIZES)
            ):
                if score >= 80 or embedding_score > 0.47:
                    return Utility.refine_us_size_string(normalized)

        return "Not present"
        
    @staticmethod
    def extract_condition_from_title(title: str) -> str:
        if not title:
            return "Good"

        title = title.lower()
        patterns = [
            (r'\bbrand\s*new\b|\bnew\b', "New"),
            (r'\blike\s*new\b|\bworn\s*once\b|\bbarely\s*worn\b|\bgently\s*used\b', "Like New"),
            (r'\bexcellent\b|\b9[\/]?10\b', "Excellent"),
            (r'\bgood\b|\b8[\/]?10\b|\bminor\s*wear\b', "Good"),
            (r'\bfair\b|\b6[\/]?10\b|\bvisible\s*wear\b', "Fair"),
        ]
        for pattern, condition in patterns:
            if re.search(pattern, title):
                return condition

        return "Good"
    
    def refine_location_string(location: str) -> str:
        if not location:
            return "US"
        location = location.lower().strip()

        country_patterns = [
            (r'\b(u\.?s\.?a?|united states( of america)?)\b', 'US'),
            (r'\b(u\.?k\.?|united kingdom|great britain|england)\b', 'UK'),
            (r'\b(eu|europe|european union)\b', 'EU'),
            (r'\bjapan\b', 'Japan'),
            (r'\bcanada\b', 'Canada'),
            (r'\baustralia\b', 'Australia'),
            (r'\bitaly\b', 'Italy'),
            (r'\bfrance\b', 'France'),
            (r'\bgermany\b', 'Germany'),
            (r'\bchina\b', 'China'),
            (r'\bhong kong\b', 'Hong Kong'),
            (r'\bswitzerland\b', 'Switzerland'),
            (r'\bspain\b', 'Spain'),
            (r'\bnetherlands\b', 'Netherlands'),
            (r'\buae\b', 'UAE'),
            (r'\bdubai\b', 'UAE'),
            (r'\bsingapore\b', 'Singapore'),
        ]

        for pattern, standard in country_patterns:
            if re.search(pattern, location):
                return standard

        return "US"
    
    @staticmethod
    def combined_similarity(title: str, query: str, threshold: float = 0.75) -> bool:
        try:
            def clean_text(s: str) -> str:
                return re.sub(r'[\W_]+', ' ', s.lower()).strip()

            t, q = clean_text(title), clean_text(query)
            fuzzy_score = fuzz.WRatio(t, q) / 100.0

            emb1 = _sbert.encode([t], convert_to_tensor=True)
            emb2 = _sbert.encode([q], convert_to_tensor=True)
            sem1 = util.cos_sim(emb1, emb2).item()

            weights = [0.4, 0.6]
            scores = [fuzzy_score, sem1]
            avg_score = sum(w * s for w, s in zip(weights, scores))

            return avg_score >= threshold
        except Exception as e:
            print(f"[combined_similarity] error: {e}")
            return False