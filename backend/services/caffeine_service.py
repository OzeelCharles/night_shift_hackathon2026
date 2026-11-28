"""Compteur de caféine : ajout d'une boisson, suppression, et vue d'ensemble pour la page."""
from datetime import datetime, timedelta

from backend.business import caffeine
from backend.business.text import bounded_int, iso, parse_time, to_datetime
from backend.dao import caffeine as dao
from backend.dao import preferences as prefs_dao

from .common import owned
from .report import Report

HISTORY_DAYS = 7


def _sleep_times(user_id):
    row = prefs_dao.get(user_id)
    return (parse_time(row["bedtime"], caffeine.BEDTIME), parse_time(row["wake_time"], caffeine.WAKE_TIME))


def _status(user, intakes, now):
    bedtime, wake = _sleep_times(user["id"])
    return caffeine.status(intakes, now, user["level"], bedtime, wake)


def save_sleep(user_id, form) -> Report:
    bed, wake = parse_time(form.get("bedtime")), parse_time(form.get("wake_time"))
    if bed is None or wake is None:
        return Report().error("Indique ton heure de coucher et ton heure de réveil.")
    length = (datetime.combine(datetime.min, wake) - datetime.combine(datetime.min, bed)) % timedelta(days=1)
    if not timedelta(hours=3) <= length <= timedelta(hours=14):
        return Report().error("Ta nuit doit durer entre 3 h et 14 h.")
    prefs_dao.save_sleep(user_id, bed.strftime("%H:%M"), wake.strftime("%H:%M"))
    return Report().success("Horaires de sommeil enregistrés.")


def _intakes(rows):
    return [(to_datetime(r["taken_at"]), r["mg"]) for r in rows]


def add(user, form, now: datetime | None = None) -> Report:
    """Enregistre une boisson (catalogue, ou nom + mg saisis) bue maintenant ou à l'heure indiquée aujourd'hui."""
    now = now or datetime.now()
    report = Report()
    key = form.get("drink", "")
    if key in caffeine.DRINKS:
        label, mg = caffeine.DRINKS[key]
    else:
        lo, hi = caffeine.CUSTOM_MG_RANGE
        mg = bounded_int(form.get("custom_mg"), 0, 0, hi)
        label = (form.get("custom_label") or "").strip()[:40] or "Autre boisson"
        if mg < lo:
            return report.error(f"Choisis une boisson, ou indique sa teneur en caféine ({lo} à {hi} mg).")
    taken = now
    if form.get("time"):
        t = parse_time(form["time"])
        if t is None:
            return report.error("Heure invalide.")
        taken = datetime.combine(now.date(), t)
        if taken > now:
            return report.error("Cette heure est dans le futur : indique l'heure à laquelle tu l'as bue.")
    before = _status(user, _intakes(dao.since(user["id"], iso(now - timedelta(hours=caffeine.LOOKBACK_HOURS)))), now)
    dao.add(user["id"], label, mg, iso(taken))
    after = overview(user, now)["status"]
    report.success(f"{label} ajouté ({mg} mg).")
    if after["over"] and not before["over"]:
        report.warning("Tu viens de dépasser le seuil recommandé pour aujourd'hui. Pense à l'eau, et stop canette.")
    elif after["sleep_risk"] and not before["sleep_risk"]:
        report.warning(f"Il te restera environ {after['at_bedtime']} mg de caféine à l'heure du coucher : "
                       "ton endormissement risque d'en pâtir.")
    return report


def remove(user_id, intake_id) -> Report:
    owned(dao.get_owned(intake_id, user_id))  # 404 si la prise n'est pas à l'utilisateur
    dao.delete(intake_id, user_id)
    return Report().info("Prise supprimée.")


def overview(user, now: datetime | None = None) -> dict:
    """Données de la page : état du jour, prises du jour, historique des 7 derniers jours, série sans caféine."""
    now = now or datetime.now()
    uid = user["id"]
    rows = dao.since(uid, iso(now - timedelta(hours=caffeine.LOOKBACK_HOURS)))
    intakes = _intakes(rows)
    today = now.date()
    totals = {r["day"]: r["mg"] for r in dao.daily_totals(uid, (today - timedelta(days=29)).isoformat())}
    limits = caffeine.limits_for(user["level"])
    history = [{"day": today - timedelta(days=i), "mg": totals.get((today - timedelta(days=i)).isoformat(), 0)}
              for i in range(HISTORY_DAYS - 1, -1, -1)]
    free_streak = None  # sans aucune prise sur 30 jours, une « série » n'aurait pas de sens
    if totals:
        free_streak = 0
        while free_streak < 30 and totals.get((today - timedelta(days=free_streak)).isoformat(), 0) == 0:
            free_streak += 1
    return {
        "status": _status(user, intakes, now), "sleep_times": _sleep_times(uid),
        "today_intakes": [dict(r) for r in rows if r["taken_at"][:10] == today.isoformat()][::-1],
        "history": history, "history_max": max([limits.daily] + [h["mg"] for h in history]),
        "free_streak": free_streak, "drinks": caffeine.DRINKS, "now": now,
    }
