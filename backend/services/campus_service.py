"""Campus : affluence de la bibliothèque de l'école et des restaurants Crous alentour, menu du jour."""
from __future__ import annotations

from datetime import datetime

from werkzeug.exceptions import NotFound

from backend.business import campus as rules
from backend.dao import campus_web
from backend.dao import occupancy as dao

from . import suggestion_service
from .report import Report


def _lives(keys) -> dict:
    """Niveau « en direct » de chaque lieu, d'après les signalements des 45 dernières minutes."""
    by_key: dict = {}
    for r in dao.recent(keys, rules.LIVE_WINDOW_MIN):
        by_key.setdefault(r["place_key"], []).append((r["level"], r["age"]))
    return {k: rules.live_level(v) for k, v in by_key.items()}


def _gauge(live, profile, now) -> dict:
    """Ce qu'on affiche : le signalement récent s'il y en a, sinon la fréquentation habituelle estimée."""
    typical = profile.get(now.hour)
    return {"live": live, "typical": typical,
            "typical_label": rules.LEVELS[rules.level_for_pct(typical)][0] if typical else None,
            "level": live["level"] if live else (rules.level_for_pct(typical) if typical else None),
            "pct": live["pct"] if live else typical}


# ---------------------------------------------------------------- bibliothèque
def bu_page(user, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    school = (user["school"] or "").strip()
    base = {"school": school or None, "levels": rules.LEVELS, "cooldown": rules.REPORT_COOLDOWN_MIN,
            "window": rules.LIVE_WINDOW_MIN, "now": now}
    if not school:
        return base
    key = rules.bu_key(school)
    profile = rules.typical_profile("bu", now.weekday())
    return {**base, "city": suggestion_service.school_city(school), "gauge": _gauge(_lives([key]).get(key), profile, now),
            "chart": rules.chart_hours(profile, now), "quiet": rules.quiet_hours(profile, now.hour),
            "closed_today": not profile, "open_now": now.hour in profile}


# ---------------------------------------------------------------- restaurants Crous
def _restaurant(r, now, live) -> dict:
    state = rules.service_state(r.get("jours_ouvert"), now)
    profile = rules.typical_profile("crous", now.weekday(), state["meals"])
    return {**r, "state": state, "gauge": _gauge(live, profile, now), "profile": profile}


def restos_page(user, city: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    city = " ".join((city or "").split())[:80]
    school_city = suggestion_service.school_city(user["school"]) if user["school"] else None
    place = city or school_city
    base = {"city_input": city, "school_city": school_city, "levels": rules.LEVELS, "now": now,
            "radius": rules.RADIUS_KM, "restaurants": [], "place": place}
    if not place:
        return {**base, "need_city": True}
    try:
        located = campus_web.geocode_city(place)
        if located is None:
            return {**base, "error": f"Je ne trouve pas la ville « {place} »."}
        near, inside = rules.nearby(campus_web.restaurants(), located[0], located[1])
    except campus_web.Unavailable:
        return {**base, "error": "Les données des restaurants Crous sont momentanément indisponibles. Réessaie dans un instant."}
    lives = _lives([rules.crous_key(r["code"]) for r in near])
    return {**base, "place": located[2], "inside": inside,
            "restaurants": [_restaurant(r, now, lives.get(rules.crous_key(r["code"]))) for r in near]}


def resto_page(user, code: int, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    try:
        found = next((r for r in campus_web.restaurants() if r["code"] == code), None)
    except campus_web.Unavailable:
        raise NotFound() from None
    if found is None:
        raise NotFound()
    key = rules.crous_key(code)
    r = _restaurant(found, now, _lives([key]).get(key))
    try:
        menu = rules.menu_for(campus_web.menu(code), now.date())
        menu_error = None
    except campus_web.Unavailable:
        menu, menu_error = {}, True
    return {"r": r, "chart": rules.chart_hours(r["profile"], now), "quiet": rules.quiet_hours(r["profile"], now.hour),
            "menu": menu, "menu_error": menu_error, "levels": rules.LEVELS, "cooldown": rules.REPORT_COOLDOWN_MIN,
            "window": rules.LIVE_WINDOW_MIN, "now": now}


def menu_preview(code: int, now: datetime | None = None) -> dict:
    """Menu du midi du jour, en bref (chargé à part pour ne pas ralentir la liste)."""
    now = now or datetime.now()
    try:
        menu = rules.menu_for(campus_web.menu(code), now.date())
    except campus_web.Unavailable:
        return {"error": True}
    return {"meals": {meal: cats for meal, cats in menu.items() if meal in ("midi", "soir")}}


# ---------------------------------------------------------------- signalement
def report(user, form) -> Report:
    level = rules.parse_level(form.get("level"))
    if level is None:
        return Report().error("Choisis un niveau d'affluence.")
    place = form.get("place", "")
    if place == "bu":
        key = rules.bu_key(user["school"] or "")
        if key is None:
            return Report().error("Indique ton établissement dans ton profil pour signaler l'affluence de ta bibliothèque.")
    elif place.startswith("crous:") and place[6:].isdigit():
        code = int(place[6:])
        try:
            if not any(r["code"] == code for r in campus_web.restaurants()):
                return Report().error("Restaurant inconnu.")
        except campus_web.Unavailable:
            return Report().error("Impossible de vérifier ce restaurant pour le moment. Réessaie dans un instant.")
        key = rules.crous_key(code)
    else:
        return Report().error("Lieu inconnu.")
    age = dao.last_by_user(user["id"], key)
    if age is not None and age < rules.REPORT_COOLDOWN_MIN:
        wait = max(1, round(rules.REPORT_COOLDOWN_MIN - age))
        return Report().info(f"Tu as déjà signalé ce lieu à l'instant : merci ! Tu pourras le refaire dans {wait} min.")
    dao.add(user["id"], key, level)
    return Report().success(f"Merci ! « {rules.LEVELS[level][0]} » est visible par les autres pendant {rules.LIVE_WINDOW_MIN} min.")
