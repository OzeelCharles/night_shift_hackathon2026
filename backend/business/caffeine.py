"""Règles de la caféine : boissons, seuils recommandés, caféine encore active dans le corps.

Repères (approximatifs, à but préventif, pas médical) :
- l'EFSA recommande de ne pas dépasser 400 mg par jour et 200 mg en une prise pour un adulte,
  et environ 3 mg par kg pour un enfant ou un adolescent (on suppose ~50 kg) ;
- la demi-vie de la caféine est d'environ 5 h : toutes les 5 h, la quantité active est divisée par deux.
"""
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta

# clé -> (libellé, mg de caféine). Valeurs moyennes constructeur, arrondies.
DRINKS = {
    "redbull_250": ("Red Bull 250 ml", 80),
    "redbull_355": ("Red Bull 355 ml", 114),
    "monster_500": ("Monster 500 ml", 160),
    "rockstar_500": ("Rockstar 500 ml", 160),
    "celsius_355": ("Celsius 355 ml", 200),
    "cafe_expresso": ("Expresso", 65),
    "cafe_long": ("Café allongé", 95),
    "the": ("Thé", 40),
    "cola_330": ("Cola 33 cl", 32),
}
REFERENCE_MG = 80           # une « canette standard » : sert à dire quand on peut en reprendre une
CUSTOM_MG_RANGE = (1, 500)  # saisie manuelle d'une autre boisson

HALF_LIFE_HOURS = 5.0
BEDTIME = time(23, 0)       # valeurs par défaut, réglables par l'utilisateur
WAKE_TIME = time(7, 0)
SLEEP_LOSS_MIN_PER_MG = 0.35  # minutes de sommeil perdues par mg encore actif au coucher (≈ -1 h pour 400 mg pris 6 h avant)
SLEEP_LOSS_MAX_MIN = 180
SLEEP_NEED_HOURS = {"adult": 8, "minor": 9}  # besoin de sommeil recommandé (adolescents : 8 à 10 h)
SLEEP_RESIDUAL_MG = 50      # au-delà, la caféine encore active à l'heure du coucher gêne l'endormissement
LOOKBACK_HOURS = 48         # au-delà, ce qui reste actif est négligeable (< 0,2 %)

MINOR_LEVELS = {"college", "lycee"}


@dataclass(frozen=True)
class Limits:
    daily: int   # mg par jour
    active: int  # mg actifs en même temps dans le corps


def limits_for(level: str) -> Limits:
    """Seuils selon le niveau d'études du profil : plus bas au collège et au lycée."""
    return Limits(daily=150, active=100) if level in MINOR_LEVELS else Limits(daily=400, active=200)


# Paliers de la mascotte : (borne haute exclue de la part du seuil quotidien consommée, emoji, titre, message)
ZEN = ("😴", "Zen", "Aucune caféine au compteur. Ton cerveau tourne tout seul, respect.")
TIERS = [
    (0.30, "🙂", "Éveillé", "Un petit boost, tu es dans le vert. Profite, mais garde de la marge."),
    (0.60, "😬", "Bien chargé", "Tu avances vite. Un verre d'eau et une pause avant la prochaine, non ?"),
    (1.00, "🤪", "Hyperactif", "Tes révisions vont à 200 à l'heure, ton sommeil de cette nuit un peu moins."),
]
RED = ("🚨", "Zone rouge", "Seuil du jour atteint. Stop canette : eau, marche, et on se revoit demain.")


def decay(mg: float, hours: float) -> float:
    return mg * math.pow(0.5, hours / HALF_LIFE_HOURS)


def active_mg(intakes, at: datetime) -> float:
    """Caféine encore active à l'instant `at`. `intakes` : couples (datetime, mg)."""
    return sum(decay(mg, (at - when).total_seconds() / 3600) for when, mg in intakes if when <= at)


def day_total(intakes, day) -> int:
    return sum(mg for when, mg in intakes if when.date() == day)


def tier_for(ratio: float) -> tuple[str, str, str]:
    """(emoji, titre, message) de la mascotte pour la part du seuil quotidien consommée."""
    if ratio <= 0:
        return ZEN
    for upper, *tier in TIERS:
        if ratio < upper:
            return tuple(tier)
    return RED


def next_ok(intakes, now: datetime, limits: Limits) -> datetime | None:
    """Premier instant où l'on peut reprendre une canette standard sans dépasser les seuils ;
    None si c'est possible dès maintenant."""
    target = limits.active - REFERENCE_MG
    current = active_mg(intakes, now)
    when = now
    if current > target:
        when = now + timedelta(hours=HALF_LIFE_HOURS * math.log2(current / target))
    if day_total(intakes, now.date()) + REFERENCE_MG > limits.daily:
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min)
        when = max(when, midnight)
    # arrondi à la minute supérieure : on ne promet jamais « 14:30 » pour 14:29:10
    if when > now:
        when = (when + timedelta(seconds=59)).replace(second=0, microsecond=0)
        return when
    return None


def sleep_need_minutes(level: str) -> int:
    return SLEEP_NEED_HOURS["minor" if level in MINOR_LEVELS else "adult"] * 60


def next_night(now: datetime, bedtime: time, wake: time) -> tuple[datetime, datetime]:
    """(coucher, réveil) de la nuit en cours ou à venir : la première dont le réveil n'est pas encore passé."""
    length = (datetime.combine(now.date(), wake) - datetime.combine(now.date(), bedtime)) % timedelta(days=1)
    for offset in (-1, 0, 1):
        bed = datetime.combine(now.date() + timedelta(days=offset), bedtime)
        if bed + length > now:
            return bed, bed + length
    raise AssertionError("inatteignable")


def last_drink_time(intakes, bed: datetime, now: datetime) -> tuple[datetime | None, bool]:
    """Heure limite pour boire une canette standard sans dépasser SLEEP_RESIDUAL_MG au coucher.
    Renvoie (heure, plus_de_caféine) : plus_de_caféine = True si le seuil est déjà atteint sans nouvelle boisson ;
    l'heure est None dans ce cas."""
    room = SLEEP_RESIDUAL_MG - active_mg(intakes, bed)
    if room <= 0:
        return None, True
    if room >= REFERENCE_MG:
        return bed, False  # n'importe quand avant le coucher
    hours = HALF_LIFE_HOURS * math.log2(REFERENCE_MG / room)
    return (bed - timedelta(hours=hours)).replace(second=0, microsecond=0), False


def status(intakes, now: datetime, level: str, bedtime: time = BEDTIME, wake: time = WAKE_TIME) -> dict:
    """Tout ce que la page affiche, calculé à partir des prises (datetime, mg)."""
    limits = limits_for(level)
    total = day_total(intakes, now.date())
    ratio = total / limits.daily
    emoji, title, message = tier_for(ratio)
    wait = next_ok(intakes, now, limits)
    bed, wake_dt = next_night(now, bedtime, wake)
    in_night = bed <= now
    at_bed = None if in_night else active_mg(intakes, bed)
    planned = int((wake_dt - bed).total_seconds() // 60)
    lost = 0 if at_bed is None else min(SLEEP_LOSS_MAX_MIN, round(at_bed * SLEEP_LOSS_MIN_PER_MG))
    need = sleep_need_minutes(level)
    cutoff, no_more = (None, False) if in_night else last_drink_time(intakes, bed, now)
    return {
        "limits": limits, "total": total, "pct": min(100, round(ratio * 100)), "ratio": ratio,
        "emoji": emoji, "title": title, "message": message,
        "active": round(active_mg(intakes, now)),
        "next_ok": wait, "can_drink": wait is None,
        "bed": bed, "wake": wake_dt, "in_night": in_night,
        "at_bedtime": None if at_bed is None else round(at_bed),
        "sleep_risk": at_bed is not None and at_bed > SLEEP_RESIDUAL_MG,
        "sleep_planned": planned, "sleep_lost": lost, "sleep_estimated": planned - lost, "sleep_need": need,
        "sleep_short": planned - lost < need,
        "cutoff": cutoff, "cutoff_passed": cutoff is not None and cutoff < now, "no_more_today": no_more,
        "over": total > limits.daily,
    }
