"""Liste embarquée des termes interdits (backend/data/mots_interdits.txt), lue une fois par processus."""
import functools
import os

from backend.business import moderation

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mots_interdits.txt")


@functools.lru_cache(maxsize=1)
def terms() -> moderation.Terms:
    with open(PATH, encoding="utf-8") as f:
        return moderation.compile_terms(f)
