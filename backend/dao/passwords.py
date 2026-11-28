"""Liste embarquée des mots de passe courants (backend/data), lue une fois par processus. Aucun réseau."""
import functools
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mots_de_passe_courants.txt")


@functools.lru_cache(maxsize=1)
def common() -> frozenset:
    with open(PATH, encoding="utf-8") as f:
        return frozenset(line.strip().lower() for line in f if line.strip() and not line.startswith("#"))
