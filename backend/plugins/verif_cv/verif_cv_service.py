"""Vérificateur de CV : dépôt (texte extrait puis fichier supprimé), alertes ATS, correspondance avec les
offres suivies dans « Stages », conseils de Groq sur demande."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import zipfile

from flask import abort, current_app, g

from backend.business import extractor
from backend.services.report import Report

from . import verif_cv_database as db
from . import verif_cv_groq as ai
from . import verif_cv_rules as rules

ALLOWED = ("pdf", "docx", "odt")
MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 60000
WORDS_PER_PAGE = 450   # estimation pour un DOCX ou un ODT qui ne dit pas son nombre de pages


def _owned(row):
    if row is None:
        abort(404)
    return row


# ---------------------------------------------------------------- dépôt
def _tmp_dir() -> str:
    """Dossier du fichier temporaire (réglable par VERIF_CV_TMP, utile aux tests) ; jamais UPLOAD_FOLDER."""
    path = current_app.config.get("VERIF_CV_TMP") or tempfile.gettempdir()
    os.makedirs(path, exist_ok=True)
    return path


def _declared_pages(path: str, ext: str) -> int | None:
    """Nombre de pages enregistré par Word (docProps/app.xml) ou LibreOffice (meta.xml), s'il existe."""
    member, pattern = ("docProps/app.xml", r"<Pages>(\d+)</Pages>") if ext == "docx" else \
        ("meta.xml", r'page-count="(\d+)"')
    try:
        with zipfile.ZipFile(path) as z:
            found = re.search(pattern, z.read(member).decode("utf-8", "ignore"))
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    return int(found.group(1)) if found and int(found.group(1)) > 0 else None


def _docx_tables(path: str) -> tuple[int, list[str]]:
    """Nombre de tableaux du DOCX et texte de leurs cellules (que l'extracteur commun ignore)."""
    try:
        import docx
        d = docx.Document(path)
    except Exception:
        return 0, []
    lines, seen = [], set()
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                if id(cell._tc) in seen:     # une cellule fusionnée revient plusieurs fois
                    continue
                seen.add(id(cell._tc))
                if cell.text.strip():
                    lines.append(cell.text.strip())
    return len(d.tables), lines


def _odt_tables(path: str) -> int:
    try:
        from odf import table
        from odf.opendocument import load
        return len(load(path).getElementsByType(table.Table))
    except Exception:
        return 0


def read_resume(path: str, ext: str) -> tuple[str, int, dict]:
    """(texte, nombre de pages, signaux) d'un CV enregistré dans un fichier temporaire."""
    tables = 0
    if ext == "pdf":
        parts = [t for _, t in extractor.extract_pages(path)]
        text, pages = "\n".join(parts), max(len(parts), 1)
        per_page = [len(t.strip()) for t in parts]
    else:
        text, _ = extractor.extract(path)
        if ext == "docx":
            tables, cells = _docx_tables(path)
            if cells:
                text = text + "\n" + "\n".join(cells)
        else:
            tables = _odt_tables(path)
        pages = _declared_pages(path, ext) or max(1, math.ceil(len(text.split()) / WORDS_PER_PAGE))
        per_page = [len(text.strip()) // pages] * pages
    return text[:MAX_TEXT], pages, {"tables": tables, "chars_per_page": per_page, "format": ext}


def upload(user, storage) -> Report:
    """Enregistre le texte du CV déposé ; le fichier temporaire est supprimé quoi qu'il arrive."""
    name = os.path.basename(str(getattr(storage, "filename", "") or "").replace("\\", "/")).strip()
    if not name:
        return Report().error("Choisis le fichier de ton CV (PDF, DOCX ou ODT).")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED:
        return Report().error("Format refusé : dépose ton CV en PDF, DOCX ou ODT.")
    data = storage.stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return Report().error("Fichier trop lourd : ton CV doit faire 5 Mo au plus.")
    if not data:
        return Report().error("Ce fichier est vide.")
    fd, path = tempfile.mkstemp(prefix="vcv_", suffix="." + ext, dir=_tmp_dir())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        text, pages, signals = read_resume(path, ext)
    finally:
        try:
            os.remove(path)
        except OSError:
            current_app.logger.warning("verif_cv : fichier temporaire non supprimé (%s)", path)
    db.save_resume(user["id"], name[:150], text, pages, json.dumps(signals))
    report = Report().success("CV analysé. Seul son texte est gardé : le fichier a été supprimé.")
    if len(text.strip()) < rules.MIN_CHARS_PER_PAGE:
        report.warning("Presque aucun texte n'a pu être lu dans ce CV : un logiciel de recrutement n'y verra rien "
                       "non plus. Refais un PDF depuis Word ou LibreOffice.")
    return report


def delete(user) -> Report:
    db.delete_resume(user["id"])
    return Report().success("Ton CV a été supprimé de Rivezli.")


# ---------------------------------------------------------------- analyse
def _signals(row) -> dict:
    try:
        return json.loads(row["signals"] or "{}")
    except ValueError:
        return {}


def _match(row, offer):
    return rules.match(row["text"], offer["description"] or "", offer["title"] or "")


def page(user, offer_id: int | None = None) -> dict:
    uid = user["id"]
    row = db.resume(uid)
    selected = _owned(db.offer(offer_id, uid)) if offer_id is not None else None
    ctx = {"resume": None, "alerts": [], "offers": db.offers(uid), "offer": selected, "match": None, "tips": [],
           "ai": ai.available(), "advice": None, "max_mb": MAX_BYTES // (1024 * 1024)}
    if row:
        signals = _signals(row)
        ctx["resume"] = {"filename": row["filename"], "uploaded_at": row["uploaded_at"], "pages": row["pages"],
                         "words": len(row["text"].split()), "tables": signals.get("tables", 0)}
        ctx["alerts"] = rules.ats_alerts(row["text"], row["pages"], signals.get("tables", 0))
        if selected:
            m = _match(row, selected)
            ctx["match"], ctx["tips"] = m, rules.match_advice(m)
    return ctx


_NO_CV = object()


def offer_score(offer) -> dict | None:
    """Pastille d'une carte d'offre (fonction de gabarit) : None sans CV ou si l'offre est trop pauvre."""
    user = g.get("user")
    if not user:
        return None
    row = g.get("_vcv_resume", _NO_CV)
    if row is _NO_CV:
        row = g._vcv_resume = db.resume(user["id"])
    if row is None:
        return None
    m = _match(row, offer)
    if not m.reliable:
        return None
    return {"score": m.score, "missing": m.missing[:3]}


# ---------------------------------------------------------------- conseils de Groq (sur clic)
def ai_advice(user, offer_id) -> tuple[str | None, Report]:
    uid = user["id"]
    offer = _owned(db.offer(offer_id, uid))
    row = db.resume(uid)
    if row is None:
        return None, Report().error("Dépose d'abord ton CV.")
    if not ai.available():
        return None, Report().info("Conseils de l'IA indisponibles sur ce serveur.")
    try:
        text = ai.advise(row["text"], dict(offer), _match(row, offer).missing)
    except ai.AdviceError as e:
        return None, Report().error(f"Groq n'a pas pu rédiger de conseils ({e}). Le reste de l'analyse reste valable.")
    return text, Report().success("Conseils rédigés par Groq : n'ajoute que ce que tu sais vraiment faire.")
