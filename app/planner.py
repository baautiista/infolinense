from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

from . import db

TZ = ZoneInfo("Europe/Madrid")
PRIORITIES = ("urgent", "today", "this_week", "future")
PRIORITY_ORDER = {name: index for index, name in enumerate(PRIORITIES)}
DAY_SLOTS = ((9, 0), (11, 0), (13, 0), (16, 0), (18, 0), (20, 0))
SPECIAL_WORDS = (
    "exclusiva", "exclusivo", "edicto", "licitación", "licitacion",
    "contratación", "contratacion", "adjudicación", "adjudicacion",
    "concurso público", "concurso publico",
)


def _slot_key(value):
    return value.replace(second=0, microsecond=0).isoformat()


def _special(row):
    kind = str(row.get("source_kind") or "").lower()
    text = " ".join(str(row.get(key) or "") for key in ("title", "excerpt", "source_name", "local_angle")).lower()
    return kind in {"bop", "procurement"} or bool(row.get("source_official")) or any(word in text for word in SPECIAL_WORDS)


def _special_label(row):
    kind = str(row.get("source_kind") or "").lower()
    text = " ".join(str(row.get(key) or "") for key in ("title", "excerpt", "source_name")).lower()
    if kind == "procurement" or any(word in text for word in ("licitación", "licitacion", "contratación", "contratacion", "adjudicación", "adjudicacion")):
        return "Licitación"
    if kind == "bop" or "edicto" in text:
        return "Edicto"
    if "exclusiva" in text or "exclusivo" in text:
        return "Exclusiva"
    return ""


def _read_rows():
    return db.rows(
        """SELECT c.*,s.kind AS source_kind,s.official AS source_official,
                  s.local_scope AS source_local_scope,a.id AS article_id,
                  a.status AS article_status,a.headline AS article_headline
           FROM candidates c
           LEFT JOIN sources s ON s.id=c.source_id
           LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'
           WHERE c.status!='archived'"""
    )


def _day_candidates(day, now):
    """Main slots first, then every half hour between 8:30 and 22:00 if the day is full."""
    main = [datetime(day.year, day.month, day.day, h, m, tzinfo=TZ) for h, m in DAY_SLOTS]
    extra = []
    t = datetime(day.year, day.month, day.day, 8, 30, tzinfo=TZ)
    end = datetime(day.year, day.month, day.day, 22, 0, tzinfo=TZ)
    while t <= end:
        if t not in main: extra.append(t)
        t += timedelta(minutes=30)
    return [c for c in main + extra if c > now + timedelta(minutes=10)]


def get_schedule():
    items = _read_rows()
    items = [item for item in items if item.get("editorial_priority") in PRIORITIES]
    items.sort(key=lambda row: (row.get("planned_at") or "9999", PRIORITY_ORDER.get(row.get("editorial_priority"), 99), -int(row.get("score") or 0)))
    return {
        "items": items,
        "timezone": "Europe/Madrid",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "suggested_slots": [f"{hour:02d}:{minute:02d}" for hour, minute in DAY_SLOTS],
        "basis": "Horarios sugeridos; la agenda se recalcula con las prioridades editoriales y las noticias nuevas.",
    }


def rebuild_schedule():
    now = datetime.now(TZ)
    rows = _read_rows()
    rows = [row for row in rows if row.get("editorial_priority") in PRIORITIES]
    occupied = set()
    locked_ids = set()
    for row in rows:
        fixed = row.get("plan_locked") or row.get("article_status") == "published"
        if fixed and row.get("planned_at"):
            try:
                dt = datetime.fromisoformat(str(row["planned_at"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ)
                occupied.add(_slot_key(dt.astimezone(TZ)))
                locked_ids.add(row["id"])
            except (ValueError, TypeError):
                pass

    rows.sort(key=lambda row: (
        PRIORITY_ORDER.get(row.get("editorial_priority"), 99),
        0 if _special(row) else 1,
        -int(row.get("score") or 0),
        str(row.get("published_at") or row.get("created_at") or ""),
    ))

    for row in rows:
        if row["id"] in locked_ids:
            continue
        if row.get("article_status") == "published":
            continue  # Ya publicada: se conserva su hora.
        bucket = row.get("editorial_priority")
        chosen = None
        reason = ""
        if bucket == "urgent":
            candidate = max(now + timedelta(minutes=10), now.replace(second=0, microsecond=0))
            while candidate.date() == now.date() and candidate.hour < 23:
                key = _slot_key(candidate)
                if key not in occupied:
                    chosen = candidate
                    break
                candidate += timedelta(minutes=30)
            if chosen is None:
                reason = "Urgente: requiere hueco inmediato; no se encontró uno hoy."
        elif bucket == "today":
            for candidate in _day_candidates(now.date(), now):
                key = _slot_key(candidate)
                if key not in occupied:
                    chosen = candidate
                    break
            if chosen is None:
                reason = "No quedan huecos libres hoy."
        elif bucket == "this_week":
            for offset in range(7):
                day = now.date() + timedelta(days=offset)
                for candidate in _day_candidates(day, now):
                    key = _slot_key(candidate)
                    if key not in occupied:
                        chosen = candidate
                        break
                if chosen:
                    break
            if chosen is None:
                reason = "No quedan huecos libres esta semana."
        elif bucket == "future":
            for offset in range(7, 31):
                day = now.date() + timedelta(days=offset)
                for hour, minute in DAY_SLOTS:
                    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
                    key = _slot_key(candidate)
                    if key not in occupied:
                        chosen = candidate
                        break
                if chosen:
                    break
            if chosen is None:
                reason = "No se encontró hueco en los próximos 30 días."

        planned = chosen.isoformat(timespec="minutes") if chosen else None
        if chosen:
            occupied.add(_slot_key(chosen))
        db.exec_(
            "UPDATE candidates SET planned_at=?,plan_reason=? WHERE id=? AND plan_locked=0",
            (planned, reason or None, row["id"]),
        )

    return get_schedule()
