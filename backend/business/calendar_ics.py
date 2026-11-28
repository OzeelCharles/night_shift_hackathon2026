"""Lecture et écriture minimales de fichiers iCalendar (.ics), sans dépendance externe.

Gère : DTSTART/DTEND/DURATION, heures UTC (suffixe Z) converties en heure locale,
récurrences RRULE FREQ=DAILY/WEEKLY (INTERVAL, COUNT, UNTIL, BYDAY) et EXDATE.
Les fuseaux TZID sont interprétés comme l'heure locale (cas d'un emploi du temps scolaire).
Les événements « journée entière » sont ignorés (souvent des marqueurs : semaine A/B, vacances...).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
# Bornes (SEC-06) : un fichier piégé ne doit ni saturer la base ni occuper le serveur.
MAX_BYTES = 2 * 1024 * 1024  # taille maximale d'un .ics importé
MAX_EVENTS = 5000  # créneaux importés au plus (les suivants sont ignorés, avec un message)
MAX_EXPAND_DAYS = 800  # pas de calcul au plus par règle de récurrence (la fenêtre utile fait moins d'un an)


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_dt(value: str) -> datetime | date:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    utc = value.endswith("Z")
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return dt


def _parse_duration(value: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return timedelta(hours=1)
    w, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _positive_int(value: str | None, default: int | None) -> int | None:
    """Entier strictement positif, sinon ValueError (règle invalide)."""
    if value is None:
        return default
    n = int(value)
    if n < 1:
        raise ValueError("entier positif attendu")
    return n


def _expand(start: datetime, rule: str, exdates: set[datetime], until_window: datetime,
            from_window: datetime | None = None) -> list[datetime]:
    """Occurrences d'une règle RRULE jusqu'à until_window. L'expansion saute directement à la période qui contient
    from_window (pas de parcours jour par jour depuis un DTSTART très ancien) et reste bornée à MAX_EXPAND_DAYS
    pas. Une règle invalide (INTERVAL=0, COUNT non numérique, UNTIL mal formé…) n'est pas dépliée."""
    try:
        parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
        freq = parts.get("FREQ")
        if freq not in ("DAILY", "WEEKLY"):
            return [start]
        interval = _positive_int(parts.get("INTERVAL"), 1)
        count = _positive_int(parts.get("COUNT"), None)
        until = None
        if "UNTIL" in parts:
            u = _parse_dt(parts["UNTIL"])
            until = u if isinstance(u, datetime) else datetime.combine(u, datetime.max.time())
    except (ValueError, OverflowError):
        return [start]
    byday = sorted({DAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in DAYS})
    if freq == "WEEKLY" and not byday:
        byday = [start.weekday()]
    out, n = [], 0
    cur = start
    week0 = start - timedelta(days=start.weekday())
    limit = until_window if until is None else min(until, until_window)
    if from_window is not None and from_window > start:
        # Saut direct : nombre de périodes entières (jour ou semaine × INTERVAL) avant la fenêtre utile
        periods = (from_window - start).days // (interval if freq == "DAILY" else 7 * interval)
        if periods > 0:
            if freq == "DAILY":
                n, cur = periods, start + timedelta(days=periods * interval)
            else:
                n = sum(1 for d in byday if d >= start.weekday()) + (periods - 1) * len(byday)
                cur = week0 + timedelta(weeks=periods * interval)
    steps = 0
    while cur <= limit and (count is None or n < count) and steps < MAX_EXPAND_DAYS:
        steps += 1
        ok = True
        if freq == "WEEKLY":
            weeks = ((cur - timedelta(days=cur.weekday())).date() - week0.date()).days // 7
            ok = cur.weekday() in byday and weeks % interval == 0
        elif freq == "DAILY":
            ok = (cur.date() - start.date()).days % interval == 0
        if ok and cur >= start:
            n += 1
            if cur not in exdates:
                out.append(cur)
        cur += timedelta(days=1)
    return out


def parse_ics(text: str, window_start: datetime, window_days: int = 180,
              keep_allday=None) -> tuple[list[dict], int]:
    """Retourne (événements [{title,start,end}], nombre d'événements journée entière ignorés).

    keep_allday(titre) -> bool : garde certains événements « journée entière » (ex. un partiel
    noté sur toute la journée), placés de 8 h à 18 h pour bloquer les révisions ce jour-là.
    """
    window_end = window_start + timedelta(days=window_days)
    events, skipped = [], 0
    raw: list[dict] = []
    cur: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()} if len(raw) < MAX_EVENTS else None
            continue
        if line == "END:VEVENT":
            if cur is not None:
                raw.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        name = key.split(";", 1)[0].upper()
        try:
            if name == "DTSTART":
                cur["start"] = _parse_dt(value)
            elif name == "DTEND":
                cur["end"] = _parse_dt(value)
            elif name == "DURATION":
                cur["duration"] = _parse_duration(value)
            elif name == "SUMMARY":
                cur["title"] = _unescape(value) or "Occupé"
            elif name == "RRULE":
                cur["rrule"] = value.strip()
            elif name == "UID":
                cur["uid"] = value.strip()
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = _parse_dt(value)
            elif name == "EXDATE":
                for v in value.split(","):
                    d = _parse_dt(v)
                    if isinstance(d, datetime):
                        cur["exdates"].add(d)
        except ValueError:
            continue

    # Une occurrence modifiée d'un cours récurrent (RECURRENCE-ID) remplace l'occurrence d'origine.
    overridden = {(ev.get("uid"), ev["recurrence_id"]) for ev in raw
                  if ev.get("uid") and isinstance(ev.get("recurrence_id"), datetime)}
    for ev in raw:
        if len(events) >= 2 * MAX_EVENTS:  # assez de matière : le tri ci-dessous garde les MAX_EVENTS premiers
            break
        try:
            skipped += _occurrences(ev, overridden, window_start, window_end, keep_allday, events)
        except (ValueError, OverflowError):  # date hors limites (an 9999, an 1…) : événement ignoré
            skipped += 1
    events.sort(key=lambda e: e["start"])
    return events[:MAX_EVENTS], skipped


def _occurrences(ev: dict, overridden: set, window_start: datetime, window_end: datetime, keep_allday,
                 events: list) -> int:
    """Ajoute à `events` les occurrences de `ev` dans la fenêtre ; renvoie 1 si l'événement est ignoré."""
    start, end = ev.get("start"), ev.get("end")
    if isinstance(start, date) and not isinstance(start, datetime) and keep_allday \
            and keep_allday(ev.get("title", "")):
        start = datetime.combine(start, datetime.min.time()).replace(hour=8)
        end = start.replace(hour=18)
    if start is None or not isinstance(start, datetime):
        return int(start is not None)
    if not isinstance(end, datetime):
        end = start + ev.get("duration", timedelta(hours=1))
    length = end - start
    uid = ev.get("uid", "")
    # Clé stable de chaque occurrence, pour repérer un cours déplacé d'un import à l'autre :
    # l'UID seul pour un événement simple ; UID + date d'origine pour une occurrence d'un cours
    # récurrent (c'est ce que désigne RECURRENCE-ID quand une occurrence est modifiée).
    if isinstance(ev.get("recurrence_id"), datetime):
        occurrences = [(start, f"{uid}|{ev['recurrence_id'].isoformat(timespec='minutes')}")]
    elif "rrule" in ev:
        occurrences = [(s, f"{uid}|{s.isoformat(timespec='minutes')}")
                       for s in _expand(start, ev["rrule"], ev["exdates"], window_end,
                                        window_start - max(length, timedelta(0)) - timedelta(days=8))
                       if (uid, s) not in overridden]
    else:
        occurrences = [(start, uid)]
    for s, key in occurrences:
        if s + length >= window_start and s <= window_end:
            events.append({"title": ev.get("title", "Occupé"), "start": s, "end": s + length,
                           "key": key if uid else ""})
    return 0


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def build_ics(sessions: list[dict], calname: str = "Planning de révision") -> str:
    """sessions : dicts avec uid, title, description, start, end (datetime naïfs, heure locale)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Planificateur de revisions//FR",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_esc(calname)}"]
    for s in sessions:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{s['uid']}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{s['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{s['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_esc(s['title'])}",
            f"DESCRIPTION:{_esc(s.get('description', ''))}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
