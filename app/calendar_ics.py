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


def _expand(start: datetime, rule: str, exdates: set[datetime], until_window: datetime) -> list[datetime]:
    parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq = parts.get("FREQ")
    if freq not in ("DAILY", "WEEKLY"):
        return [start]
    interval = int(parts.get("INTERVAL", 1))
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    until = None
    if "UNTIL" in parts:
        u = _parse_dt(parts["UNTIL"])
        until = u if isinstance(u, datetime) else datetime.combine(u, datetime.max.time())
    byday = [DAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    if freq == "WEEKLY" and not byday:
        byday = [start.weekday()]
    out, n = [], 0
    cur = start
    week0 = start - timedelta(days=start.weekday())
    limit = until_window if until is None else min(until, until_window)
    while cur <= limit and (count is None or n < count):
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


def parse_ics(text: str, window_start: datetime, window_days: int = 180) -> tuple[list[dict], int]:
    """Retourne (événements [{title,start,end}], nombre d'événements journée entière ignorés)."""
    window_end = window_start + timedelta(days=window_days)
    events, skipped = [], 0
    cur: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
            continue
        if line == "END:VEVENT":
            if cur is not None:
                start, end = cur.get("start"), cur.get("end")
                if start is None or not isinstance(start, datetime):
                    skipped += start is not None
                else:
                    if not isinstance(end, datetime):
                        end = start + cur.get("duration", timedelta(hours=1))
                    length = end - start
                    starts = (_expand(start, cur["rrule"], cur["exdates"], window_end)
                              if "rrule" in cur else [start])
                    for s in starts:
                        if s + length >= window_start and s <= window_end:
                            events.append({"title": cur.get("title", "Occupé"), "start": s, "end": s + length})
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
            elif name == "EXDATE":
                for v in value.split(","):
                    d = _parse_dt(v)
                    if isinstance(d, datetime):
                        cur["exdates"].add(d)
        except ValueError:
            continue
    return events, skipped


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
