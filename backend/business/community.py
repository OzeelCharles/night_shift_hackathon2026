"""Communauté : messagerie, partage de supports et forum. Règles pures (aucune base, aucun Flask)."""
from backend.business.extractor import ALLOWED_EXTENSIONS

# Catégories du forum : clé -> (libellé, description)
CATEGORIES = {
    "exercices": ("Exercices", "Un exercice te résiste ? Pose-le, explique où tu bloques."),
    "astuces": ("Astuces et méthodes", "Organisation, fiches, mémorisation : ce qui marche pour toi."),
    "retours": ("Retours d'expérience", "Raconte ton année, ton semestre, ta prépa, ton stage."),
    "orientation": ("Du lycée au supérieur", "Choisir sa formation, Parcoursup, réussir sa première année."),
    "concours": ("Prépas et concours", "Aborder les concours : préparation, oraux, calendrier, rangs."),
    "ecoles": ("Écoles et universités", "Questions sur une école, une filière, la vie sur le campus."),
    "libre": ("Discussion libre", "Tout le reste."),
}

# Fichiers échangeables : les formats de cours acceptés par Rivezli, plus les images (photos de feuilles)
ATTACHMENT_EXTENSIONS = ALLOWED_EXTENSIONS | {"png", "jpg", "jpeg"}
MAX_FILES = 5
MAX_FILE_BYTES = 15 * 1024 * 1024
TITLE_RANGE = (4, 140)
BODY_RANGE = (1, 5000)
PAGE_SIZE = 20


def allowed_attachment(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ATTACHMENT_EXTENSIONS


def clean_body(value) -> str:
    return "\n".join(line.rstrip() for line in (value or "").replace("\r", "").strip().split("\n"))


def validate_body(body: str) -> list[str]:
    lo, hi = BODY_RANGE
    if len(body.strip()) < lo:
        return ["Écris un message."]
    return [f"Message trop long ({hi} caractères au maximum)."] if len(body) > hi else []


def validate_topic(category: str, title: str, body: str) -> list[str]:
    errors = [] if category in CATEGORIES else ["Choisis une catégorie."]
    lo, hi = TITLE_RANGE
    if not lo <= len(title) <= hi:
        errors.append(f"Le titre doit faire entre {lo} et {hi} caractères.")
    return errors + validate_body(body)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} o"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} Ko"
    return f"{n / (1024 * 1024):.1f} Mo".replace(".", ",")


# ---------------------------------------------------------------- modération
RULES = [
    ("Respect", "Pas d'insultes, de moqueries, de harcèlement ni de menaces. On critique les idées, jamais les personnes."),
    ("Aucune haine", "Aucun propos raciste, sexiste, homophobe, discriminatoire ou qui vise une origine, une religion, un handicap."),
    ("Entraide honnête", "Aide un camarade à comprendre plutôt que de lui donner un devoir à rendre. Ne partage pas de sujets d'examen ou de concours avant l'épreuve."),
    ("Documents", "Ne partage que des supports dont tu as le droit de diffuser le contenu : tes notes, tes fiches, les cours que ton enseignant autorise à partager."),
    ("Vie privée", "Ne publie jamais les coordonnées ou les messages privés de quelqu'un sans son accord."),
    ("Pas de spam", "Pas de publicité, de démarchage ni de messages répétés. Poste dans la bonne catégorie."),
]
REASONS = {
    "haine": "Propos haineux ou discriminatoires",
    "harcelement": "Harcèlement, insultes ou menaces",
    "inapproprie": "Contenu choquant ou inapproprié",
    "triche": "Triche ou sujet d'examen divulgué",
    "droits": "Document partagé sans en avoir le droit",
    "spam": "Spam ou publicité",
    "autre": "Autre règle non respectée",
}
DECISIONS = {
    "dismiss": "Rejeter le signalement",
    "remove": "Supprimer le contenu",
    "remove_mute": "Supprimer le contenu et suspendre l'auteur",
}
DETAILS_MAX = 500
NOTE_MAX = 300


def is_staff(role: str, is_moderator) -> bool:
    """Peut modérer : administrateur, ou utilisateur nommé modérateur par un administrateur."""
    return role == "admin" or bool(is_moderator)


def can_mute(actor_role: str, target_role: str, target_is_moderator) -> bool:
    """Un administrateur peut suspendre tout le monde sauf un autre administrateur ; un modérateur, les simples membres."""
    if target_role == "admin":
        return False
    return actor_role == "admin" or not target_is_moderator


def validate_report(reason: str, details: str) -> list[str]:
    if reason not in REASONS:
        return ["Choisis la raison du signalement."]
    if reason == "autre" and not details:
        return ["Explique en quelques mots quelle règle n'est pas respectée."]
    return [f"Ton explication est trop longue ({DETAILS_MAX} caractères au maximum)."] if len(details) > DETAILS_MAX else []
