"""Outil de l'assistant fourni par l'atelier : scores des QCM d'une matière, avec leur courbe."""
from backend.business import assistant as rules
from backend.business.text import date_fr
from backend.core.tools import Tool, params, text_param
from backend.services.assistant_tools import find_subject

from . import store


def graphique_qcm(user, matiere: str = "", **_) -> dict:
    s, error = find_subject(user["id"], matiere)
    if error:
        return error
    tries = [a for a in store.attempts(s["id"]) if a["score"] is not None]
    if not tries:
        return {"erreur": f"Aucun QCM rendu en {s['name']} pour l'instant (onglet « Réviser »)."}
    points = [{"label": date_fr(a["submitted_at"][:10]), "value": a["score"]} for a in tries]
    title = f"Scores des QCM en {s['name']} (sur 20)"
    return {"titre": title, "nombre_de_qcm": len(tries), "premier": tries[0]["score"], "dernier": tries[-1]["score"],
            "moyenne": round(sum(a["score"] for a in tries) / len(tries), 2),
            "par_chapitre": sorted({a["chapter"] or "toute la matière" for a in tries}),
            "chart": rules.chart(title, points)}


TOOLS = [Tool("graphique_qcm",
              "Scores des QCM de l'atelier de révision pour une matière (sur 20), avec la courbe de leur évolution.",
              graphique_qcm, params(("matiere",), matiere=text_param("Nom de la matière.")))]
