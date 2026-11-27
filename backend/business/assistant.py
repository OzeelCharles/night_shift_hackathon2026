"""Assistant : consignes données au modèle, fenêtre d'historique, résumés, graphiques (règles pures).

L'historique d'une conversation est envoyé en deux parties pour rester fluide sans dépasser le quota
gratuit de Groq : les derniers échanges tels quels, et un résumé des plus anciens.
"""
from __future__ import annotations

import json
from datetime import date

from .text import JOURS, date_fr, number_fr

RECENT_MESSAGES = 8          # messages envoyés tels quels (4 échanges)
SUMMARIZE_EVERY = 6          # on résume dès que 6 messages sont sortis de la fenêtre
MAX_TOOL_ROUNDS = 4          # appels d'outils enchaînés au plus pour une question
MAX_TOOL_CHARS = 3500        # taille maximale d'un résultat d'outil envoyé au modèle
MAX_USER_CHARS = 2000        # longueur maximale d'un message de l'utilisateur
MAX_ATTACHMENT_CHARS = 6000  # contenu d'une photo ou d'un PDF envoyé avec la question (quota Groq : 8 000 jetons/min)
MAX_ATTACHMENT_CHARS_CLAUDE = 60000  # Claude lit de longs documents : environ 15 000 jetons
TOOL_STATUS = {             # ce que la bulle affiche pendant qu'un outil travaille
    "chercher_dans_mes_cours": "Je cherche dans tes supports de cours…",
    "programme_du_jour": "Je regarde ton planning…",
    "chapitres": "Je regarde tes chapitres…",
    "notes": "Je regarde tes notes…",
    "graphique_notes": "Je prépare le graphique de tes notes…",
    "graphique_qcm": "Je prépare le graphique de tes QCM…",
    "seances_a_rattraper": "Je regarde tes séances non faites…",
    "preparer_rattrapage": "Je calcule comment rattraper ta séance…",
}
HISTORY_ATTACHMENT_CHARS = 1200  # ce qui en est renvoyé ensuite dans l'historique
DEFAULT_ATTACHMENT_QUESTION = "Explique-moi ce document."

# Lecture d'une photo jointe par le modèle de vision (avant de la donner à l'assistant)
VISION_PROMPT = (
    "Recopie fidèlement tout le contenu de cette image (notes de cours, énoncé, tableau, exercice…), dans l'ordre "
    "de lecture, en Markdown. Les formules mathématiques sont en LaTeX : $…$ en ligne, $$…$$ pour une formule "
    "isolée. Décris brièvement entre crochets les schémas ou graphiques. N'ajoute aucun commentaire. "
    "Termine par une ligne « Matière probable : … ». Si l'image est illisible, réponds seulement ILLISIBLE.")

RULES = """Tu es l'assistant de Rivezli, une application qui organise les révisions d'un étudiant.
Tu tutoies l'étudiant, tu réponds en français, de façon chaleureuse, concrète et aérée.
Adapte la longueur à la demande : court pour le planning ou une question simple ; complet et structuré pour
une explication de cours (idée générale, définitions, étapes du raisonnement, exemple), sans t'arrêter en
cours de route.

Règles :
- Pour tout ce qui concerne SES données (planning, séances, examens, matières, chapitres, notes, scores de QCM),
  appelle d'abord l'outil adapté. N'invente jamais une séance, une date, une note ou un score.
- « Qu'est-ce que je fais aujourd'hui ? » : appelle programme_du_jour. S'il y a des séances prévues, présente-les
  dans l'ordre. S'il n'y en a pas, propose un programme réaliste dans les matières dont l'examen est le plus proche
  (chapitres pas encore étudiés d'abord, révisions courtes ensuite), en précisant qu'il n'est pas encore dans son
  planning. Ne présente jamais comme « déjà étudié » un chapitre listé dans chapitres_pas_encore_etudies : si
  tous le sont, ne propose pas de révision de chapitres vus.
- « Comment réviser telle matière ? » : si l'étudiant ne précise pas la partie du cours, demande-la-lui ; appelle
  chapitres pour lui proposer la liste de ses chapitres (en signalant ceux déjà étudiés). Ensuite seulement,
  donne une méthode adaptée (rappel actif, révisions espacées, exercices, annales).
- Questions sur ses notes ou ses progrès : appelle notes. Commente l'évolution honnêtement (moyenne sur 20,
  effet de la dernière note), sans dramatiser, puis propose une ou deux actions précises.
- Pour comparer ou visualiser l'évolution de scores : appelle graphique_notes (notes du dossier scolaire) ou
  graphique_qcm (scores des QCM de l'atelier) si cet outil existe. Le graphique s'affiche tout seul sous ta réponse :
  ne recopie pas tous les points, commente la tendance en une ou deux phrases.
- Méthodes de travail : ne conseille que des méthodes dont l'efficacité est démontrée (pratique de la récupération
  / auto-test, révisions espacées, alternance des sujets, explication à voix haute, exercices corrigés).
- Photo ou PDF joint : son contenu, lu automatiquement, t'est donné entre « [Pièce jointe …] » et « [Fin de la
  pièce jointe] ». Appuie-toi dessus : repère la matière concernée (outil chapitres si utile), puis réponds de façon
  rigoureuse, étape par étape, en justifiant chaque formule. Si la lecture semble incomplète ou douteuse, dis-le.
  Pour résumer ou expliquer le document joint, appuie-toi d'abord sur lui : ne cherche dans ses supports de cours
  que si la question le demande ou si le document ne suffit pas.
- Question de cours (« explique-moi… », « c'est quoi… », une formule, un théorème, un exercice) : appelle d'abord
  chercher_dans_mes_cours pour t'appuyer sur SES supports (ses notations, ses définitions). Si tu connais la
  matière (page ouverte, conversation), passe-la en paramètre. Cite chaque information tirée d'un passage avec
  son numéro entre crochets, juste après la phrase : [S1], [S2]. N'invente jamais de numéro. Si les passages ne
  répondent pas à la question, dis-le clairement (« je n'ai pas trouvé ça dans tes supports »), puis réponds
  avec tes connaissances générales en le signalant. Si des passages viennent de plusieurs matières et que la
  question est ambiguë, demande de quelle matière il s'agit.
- Séance ratée (« j'ai raté ma séance », « je n'ai pas pu réviser », « je vais rater celle de ce soir ») : appelle
  preparer_rattrapage (avec la matière si elle est connue). L'outil ne modifie rien : il calcule de vraies options
  que l'étudiant valide lui-même avec les boutons affichés sous ta réponse. Présente brièvement chaque option
  (nouvelles séances, travail qui resterait sans créneau avant l'examen, examen trop proche), recommande-en une
  et invite-le à cliquer. Si replacer suffit, dis-le simplement. Ne prétends JAMAIS avoir modifié son planning
  toi-même. Sois rassurant : rater une séance arrive, l'important est de rattraper intelligemment.
- Pour toute autre modification du planning (horaires, préférences), indique où la faire dans l'application
  (Encadrement > Mes réglages / Mon agenda / la page de la matière).
- Matière : quand le nom écrit par l'étudiant correspond clairement à une de ses matières (« la régression » →
  « Régression linéaire et généralisée »), utilise-la directement, sans lui demander de confirmer. Ne demande
  laquelle que si plusieurs de ses matières conviennent vraiment, ou si aucune ne correspond.
- Formules mathématiques : toujours en LaTeX, $…$ dans une phrase et $$…$$ pour une formule isolée sur sa
  propre ligne (elles sont affichées joliment). Exemple : $$X_t = \\phi_1 X_{t-1} + \\phi_2 X_{t-2} + \\cdots
  + \\phi_p X_{t-p} + \\varepsilon_t$$. Utilise \\cdots ou \\ldots, jamais le caractère « … » dans une formule.
- Mise en forme en Markdown (affiché tel quel) : paragraphes courts, listes avec « - » ou « 1. » (sous-listes
  indentées de deux espaces), **gras**, *italique*, titres ### seulement pour une réponse longue, tableau Markdown
  quand tu compares plusieurs éléments, `code` et blocs ``` pour du code. Une formule isolée $$…$$ va sur sa
  propre ligne."""


def system_prompt(profile: dict, today: date, page: str = "") -> str:
    """Consignes + contexte : date du jour, profil scolaire, matières, page où l'étudiant a ouvert la bulle."""
    who = ", ".join(p for p in (profile.get("level_label"), profile.get("school")) if p)
    lines = [RULES, "", f"Aujourd'hui : {JOURS[today.weekday()]} {date_fr(today)}."]
    if who:
        lines.append(f"Étudiant : {profile.get('username') or 'étudiant'} ({who}).")
    if profile.get("subjects"):
        lines.append("Ses matières : " + ", ".join(f"« {s} »" for s in profile["subjects"][:20]) + ".")
    if page:
        lines.append(f"Il t'écrit depuis : {page}.")
    return "\n".join(lines)


def summary_block(summary: str) -> dict | None:
    if not summary:
        return None
    return {"role": "system", "content": f"Résumé du début de la conversation (plus ancien) :\n{summary}"}


def split_history(messages: list[dict], summarized_upto: int, keep: int = RECENT_MESSAGES) -> tuple[list, list]:
    """(messages à ajouter au résumé, messages récents envoyés tels quels).

    Les messages déjà résumés ne sont plus envoyés ; on ne résume que par paquets de SUMMARIZE_EVERY.
    """
    pending = [m for m in messages if m["id"] > summarized_upto]
    if len(pending) - keep < SUMMARIZE_EVERY:
        return [], pending
    return pending[:-keep], pending[-keep:]


def attachment_limit(provider: str) -> int:
    return MAX_ATTACHMENT_CHARS_CLAUDE if provider == "anthropic" else MAX_ATTACHMENT_CHARS


def attachment_block(name: str, kind: str, text: str, limit: int = MAX_ATTACHMENT_CHARS) -> str:
    """Contenu lu d'une pièce jointe, ajouté à la question envoyée au modèle."""
    text = text.strip()
    if kind == "image" and not text:   # photo lue directement par le modèle (jointe à la question)
        return f"\n\n[Pièce jointe : photo « {name} », ci-dessus]"
    cut = len(text) > limit
    label = "photo" if kind == "image" else "PDF"
    return (f"\n\n[Pièce jointe : {label} « {name} »{' (extrait du début)' if cut else ''}]\n"
            f"{text[:limit]}{' […]' if cut else ''}\n[Fin de la pièce jointe]")


def with_attachment(m: dict) -> str:
    """Texte d'un message de l'historique, avec un extrait de sa pièce jointe éventuelle."""
    a = (m.get("meta") or {}).get("attachment")
    if not a:
        return m["content"]
    return m["content"] + attachment_block(a["name"], a["kind"], a.get("excerpt", ""), HISTORY_ATTACHMENT_CHARS)


def summary_request(old_summary: str, messages: list[dict]) -> list[dict]:
    transcript = "\n".join(f"{'Étudiant' if m['role'] == 'user' else 'Assistant'} : {with_attachment(m)}"
                           for m in messages)
    return [
        {"role": "system", "content": "Tu résumes une conversation entre un étudiant et l'assistant de révision "
                                      "Rivezli, en français, en 6 lignes au plus. Garde les faits utiles pour la "
                                      "suite : matières et chapitres évoqués, questions en suspens, décisions, "
                                      "préférences exprimées. Pas de formule de politesse."},
        {"role": "user", "content": f"Résumé précédent :\n{old_summary or '(aucun)'}\n\nNouveaux échanges :\n"
                                    f"{transcript}\n\nRésumé mis à jour :"},
    ]


def title_from(text: str) -> str:
    """Titre d'une conversation : le début du premier message."""
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57].rsplit(" ", 1)[0] + "…"


def clean_user_text(text: str) -> str:
    return (text or "").strip()[:MAX_USER_CHARS]


def parse_arguments(raw) -> dict:
    """Arguments d'un appel d'outil (texte JSON) ; {} s'ils sont illisibles."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def split_result(result: dict, source_offset: int = 0) -> tuple[str, dict | None, list[dict]]:
    """(texte envoyé au modèle, graphique pour l'interface, sources citables).

    Le graphique n'est pas envoyé au modèle en entier. Les sources d'une recherche dans les supports
    (« passages » et « sources », alignés) sont numérotées à la suite de celles déjà données : S1, S2…"""
    result = dict(result or {})
    chart_spec = result.pop("chart", None)
    if chart_spec:
        result["graphique"] = f"affiché sous ta réponse ({len(chart_spec['points'])} points)"
    sources = result.pop("sources", None) or []
    if sources:
        labels = [f"S{source_offset + i}" for i in range(1, len(sources) + 1)]
        sources = [s | {"source": label} for s, label in zip(sources, labels)]
        result["passages"] = [p | {"source": label} for p, label in zip(result.get("passages", []), labels)]
    limit = MAX_TOOL_CHARS if not sources else MAX_TOOL_CHARS * 2   # les passages du cours sont plus longs
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + "… (résultat tronqué)"
    return text, chart_spec, sources


# ---------------------------------------------------------------- graphiques
def chart(title: str, points: list[dict], y_max: float = 20, unit: str = "/ 20",
          width: float = 320, height: float = 150, left: float = 28, right: float = 12,
          top: float = 12, bottom: float = 22) -> dict | None:
    """Courbe d'évolution (une seule série) : points [{label, value}] du plus ancien au plus récent.

    La géométrie est calculée ici (abscisses régulières, ordonnée de 0 à y_max) ; l'interface dessine.
    """
    if not points:
        return None
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / (len(points) - 1) if len(points) > 1 else 0
    out = []
    for i, p in enumerate(points):
        v = max(0.0, min(float(p["value"]), y_max))
        x = left + i * step if len(points) > 1 else left + plot_w / 2
        out.append({"x": round(x, 1), "y": round(top + (1 - v / y_max) * plot_h, 1),
                    "label": p["label"], "value": round(v, 2), "text": f"{number_fr(round(v, 2))} {unit}".strip()})
    ticks = [{"y": round(top + (1 - t / y_max) * plot_h, 1), "text": number_fr(t)}
             for t in (0, y_max / 2, y_max)]
    return {"title": title, "width": width, "height": height, "left": left, "right": width - right,
            "bottom": top + plot_h, "ticks": ticks, "points": out}
