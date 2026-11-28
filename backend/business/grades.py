"""Dossier scolaire : validation des notes, moyennes, progression et géométrie des courbes.

Toutes les moyennes sont ramenées sur 20 et pondérées par les coefficients.
La progression d'une matière compare sa moyenne avec et sans sa dernière note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .text import date_fr, parse_date

SCALE = 20.0


def _number(raw, default=None):
    try:
        return float(str(raw).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def validate(form) -> tuple[dict, list[str]]:
    """Lit une note saisie (« 14,5 » sur 20, coefficient 2…) ; renvoie (données, erreurs)."""
    errors = []
    out_of = _number(form.get("out_of") or 20)
    if out_of is None or not 1 <= out_of <= 1000:
        errors.append("Le barème doit être compris entre 1 et 1000 (par exemple 20).")
        out_of = SCALE
    value = _number(form.get("value"))
    if value is None:
        errors.append("Indique la note obtenue.")
    elif not 0 <= value <= out_of:
        errors.append(f"La note doit être comprise entre 0 et {out_of:g}.")
    coefficient = _number(form.get("coefficient") or 1)
    if coefficient is None or not 0 < coefficient <= 100:
        errors.append("Le coefficient doit être un nombre positif (par exemple 1 ou 2).")
    raw_day = (form.get("day") or "").strip()
    day = parse_date(raw_day) if raw_day else date.today()  # BDD-06 : année plausible
    if day is None:
        errors.append("Date de la note invalide.")
    if day and day > date.today():
        errors.append("La date de la note ne peut pas être dans le futur.")
    return {"value": value, "out_of": out_of, "coefficient": coefficient,
            "label": (form.get("label") or "").strip()[:80], "day": day.isoformat() if day else None}, errors


def on_twenty(grade) -> float:
    return grade["value"] / grade["out_of"] * SCALE


def weighted_average(grades) -> float | None:
    total = sum(g["coefficient"] for g in grades)
    return sum(on_twenty(g) * g["coefficient"] for g in grades) / total if total else None


@dataclass
class Course:
    """Une ligne du dossier : une matière (de l'agenda ou créée) et ses notes, du plus ancien au plus récent."""
    name: str
    subject_id: int | None = None
    from_agenda: bool = False
    grades: list = field(default_factory=list)

    @property
    def average(self) -> float | None:
        return weighted_average(self.grades)

    @property
    def progress(self) -> float | None:
        """Effet de la dernière note sur la moyenne (positif = en progrès)."""
        if len(self.grades) < 2:
            return None
        return self.average - weighted_average(self.grades[:-1])

    @property
    def sparkline(self) -> list[float]:
        return [round(on_twenty(g), 2) for g in self.grades][-12:]


def overall(courses: list[Course]) -> float | None:
    """Moyenne générale : moyenne des moyennes des matières notées."""
    averages = [c.average for c in courses if c.grades]
    return sum(averages) / len(averages) if averages else None


def overall_history(courses: list[Course]) -> list[dict]:
    """Moyenne générale après chaque jour où une note a été saisie : [{day, value}]."""
    days = sorted({g["day"] for c in courses for g in c.grades})
    history = []
    for day in days:
        averages = [weighted_average([g for g in c.grades if g["day"] <= day]) for c in courses]
        averages = [a for a in averages if a is not None]
        history.append({"day": day, "label": date_fr(day), "value": round(sum(averages) / len(averages), 2)})
    return history


# ---------------------------------------------------------------- géométrie des courbes (SVG)
def sparkline_points(values: list[float], width: float = 120, height: float = 32, pad: float = 4) -> str:
    """Points d'une mini-courbe sur une échelle fixe de 0 à 20 (comparable d'une matière à l'autre)."""
    if not values:
        return ""
    step = (width - 2 * pad) / max(1, len(values) - 1)
    return " ".join(f"{pad + i * step:.1f},{pad + (1 - v / SCALE) * (height - 2 * pad):.1f}"
                    for i, v in enumerate(values))


def line_chart(history: list[dict], width: float = 960, height: float = 260,
               left: float = 36, right: float = 24, top: float = 16, bottom: float = 28) -> dict:
    """Courbe de la moyenne générale : abscisse proportionnelle au temps, ordonnée de 0 à 20."""
    if not history:
        return {}
    ordinals = [date.fromisoformat(h["day"]).toordinal() for h in history]
    span = max(1, ordinals[-1] - ordinals[0])
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(o):
        return left + (o - ordinals[0]) / span * plot_w if len(history) > 1 else left + plot_w / 2

    def y(v):
        return top + (1 - v / SCALE) * plot_h

    points = [{"x": round(x(o), 1), "y": round(y(h["value"]), 1), **h} for o, h in zip(ordinals, history)]
    return {
        "width": width, "height": height, "points": points,
        "path": " ".join(f"{p['x']},{p['y']}" for p in points),
        "area": (f"{points[0]['x']},{top + plot_h} " + " ".join(f"{p['x']},{p['y']}" for p in points)
                 + f" {points[-1]['x']},{top + plot_h}"),
        "ticks": [{"value": v, "y": round(y(v), 1)} for v in (0, 5, 10, 15, 20)],
        "x_labels": [points[0], points[-1]] if len(points) > 1 else points,
        "left": left, "right": width - right, "bottom": top + plot_h,
    }
