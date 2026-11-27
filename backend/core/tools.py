"""Outils de l'assistant : ce que le chatbot peut consulter dans Rivezli (planning, notes, scores…).

Chaque fonctionnalité peut en fournir (champ `tools` de Feature). Le modèle de langage voit le nom,
la description et les paramètres (schéma JSON) ; quand il appelle un outil, `run(user, **arguments)`
s'exécute pour l'utilisateur connecté seulement et renvoie un dict sérialisable en JSON.

Clés spéciales du résultat, retirées avant l'envoi au modèle et transmises à l'interface :
- « chart » : un graphique à afficher dans la conversation (voir business/assistant.chart).
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Tool:
    name: str                  # identifiant pour le modèle (lettres, chiffres, « _ »)
    description: str           # quand l'utiliser, en une ou deux phrases
    run: Callable[..., dict]   # run(user, **arguments) -> dict
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def text_param(description: str) -> dict:
    return {"type": "string", "description": description}


def params(required: tuple[str, ...] = (), **properties) -> dict:
    """Schéma des paramètres. Un paramètre facultatif accepte aussi null : certains modèles l'envoient
    (« matiere": null) et Groq refuse l'appel si le schéma ne l'autorise pas."""
    props = {name: (p if name in required else p | {"type": [p["type"], "null"]}) for name, p in properties.items()}
    return {"type": "object", "properties": props, "required": list(required)}
