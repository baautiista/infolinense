from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

from . import db

TZ = ZoneInfo("Europe/Madrid")
PRIORITIES = ("urgent", "today", "this_week", "future")
PRIORITY_ORDER = {name: index for index, name in enumerate(PRIORITIES)}
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


# Parrilla diaria de cada medio: pocas noticias y bien repartidas.
#   Mañana (hasta las 13:00): 3-4, con la actualidad del Ayuntamiento de ese día primero.
#   Tarde (16:00-20:00): 2-3.   Noche (hasta las 23:00): unas 2.
GRID = (("morning", (8, 30)), ("morning", (10, 0)), ("morning", (11, 30)), ("morning", (13, 0)),
        ("afternoon", (16, 0)), ("afternoon", (17, 45)), ("afternoon", (19, 30)),
        ("night", (21, 0)), ("night", (22, 30)))
DAY_SLOTS = tuple(t for _, t in GRID)
_YOUTH = re.compile(r"\b(juvenil|j[oó]venes|infantil|cadete|alev[ií]n|benjam[ií]n|escolar|colegio|instituto|estudiantes|"
                    r"concierto|fiesta|ocio|festival|feria|carnaval|chirigota|comparsa|cine|teatro|exposici[oó]n|agenda|"
                    r"fin de semana|planes|deporte|partido|balona|torneo|campeonato)", re.I)
MORNING_SECTIONS = {"OBRAS", "POLÍTICA", "CIUDAD", "SOCIEDAD", "GIBRALTAR", "SUCESOS"}
EVENING_SECTIONS = {"DEPORTES", "CULTURA", "AGENDA", "COMERCIO"}


def _section(row):
    sec = str(row.get("section") or "").upper()
    if sec:
        return sec
    from . import layout
    return layout.family_for("", row.get("title") or "")[0]


def _kind(row):
    """morning: actualidad institucional; evening: juvenil, ocio, deportes, cultura; neutral: lo demás."""
    from . import sources
    text = " ".join(str(row.get(k) or "") for k in ("title", "excerpt"))
    if sources.source_group(row) in ("Ayuntamiento", "Licitaciones y edictos") or _special(row):
        return "morning"
    if _YOUTH.search(text) or _section(row) in EVENING_SECTIONS:
        return "evening"
    if _section(row) in MORNING_SECTIONS:
        return "morning"
    return "neutral"


PERIOD_COST = {"morning": {"morning": 0, "afternoon": 2, "night": 4},
               "evening": {"morning": 3, "afternoon": 0, "night": 0.5},
               "neutral": {"morning": 0.5, "afternoon": 0.5, "night": 1}}


def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)
    except (ValueError, TypeError):
        return None


def _nearest_slot(dt):
    """Índice de la franja de la parrilla más cercana a una hora."""
    mins = dt.hour * 60 + dt.minute
    return min(range(len(GRID)), key=lambda i: abs(GRID[i][1][0] * 60 + GRID[i][1][1] - mins))


def rebuild_schedule():
    """Coloca cada noticia marcada en un hueco de la parrilla de su medio, según prioridad y contenido:
    lo del Ayuntamiento por la mañana, lo juvenil y de ocio por la tarde-noche, y nunca dos parecidas seguidas."""
    from . import sources
    now = datetime.now(TZ)
    rows = [row for row in _read_rows() if row.get("editorial_priority") in PRIORITIES]
    # ocupación: {(marca, fecha): {índice_franja: fila}}
    taken = {}

    def day_map(brand, day):
        return taken.setdefault((brand or "infolinense", day), {})

    for row in rows:
        fixed = row.get("plan_locked") or row.get("article_status") == "published"
        dt = _parse(row.get("planned_at")) if fixed else None
        if dt:
            slots = day_map(row.get("brand"), dt.date())
            i = _nearest_slot(dt)
            while i in slots and i + 1 < len(GRID):
                i += 1
            slots[i] = row
            row["_placed"] = True

    rows.sort(key=lambda row: (
        PRIORITY_ORDER.get(row.get("editorial_priority"), 99),
        0 if sources.source_group(row) == "Ayuntamiento" and str(row.get("published_at") or "")[:10] == now.date().isoformat() else 1,
        0 if _special(row) else 1,
        -int(row.get("score") or 0),
        str(row.get("published_at") or row.get("created_at") or ""),
    ))

    def cost(row, day, i, slots):
        period = GRID[i][0]
        c = PERIOD_COST[_kind(row)][period] + i * 0.15  # antes, mejor
        for j in (i - 1, i + 1):
            other = slots.get(j)
            if other is None:
                continue
            if _section(other) == _section(row):
                c += 3  # dos de la misma sección seguidas, no
            if sources.same_story(str(other.get("title") or ""), str(row.get("title") or "")):
                c += 5
        return c

    for row in rows:
        if row.get("_placed") or row.get("article_status") == "published":
            continue
        bucket = row.get("editorial_priority")
        brand = row.get("brand") or "infolinense"
        chosen, reason = None, ""
        if bucket == "urgent":  # lo antes posible, aunque la parrilla esté llena
            chosen = (now + timedelta(minutes=10)).replace(second=0, microsecond=0)
        else:
            first = 0 if bucket in ("today", "this_week") else 7
            last = {"today": 2, "this_week": 7}.get(bucket, 31)
            if row.get("plan_day"):  # «Mañana»: ese día (o los siguientes si está lleno)
                try:
                    first = max(0, (datetime.fromisoformat(row["plan_day"]).date() - now.date()).days)
                    last = first + 3
                except ValueError:
                    pass
            best = None
            for offset in range(first, last):
                day = now.date() + timedelta(days=offset)
                slots = day_map(brand, day)
                for i, (_, (h, m)) in enumerate(GRID):
                    when = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
                    if i in slots or when <= now + timedelta(minutes=10):
                        continue
                    c = cost(row, day, i, slots) + offset * (6 if bucket == "today" else 1.5)
                    if best is None or c < best[0]:
                        best = (c, day, i, when)
                if best and bucket != "future" and best[1] == day and best[0] < 2:
                    break  # buen hueco hoy: no hace falta mirar más días
            if best:
                _, day, i, chosen = best
                day_map(brand, day)[i] = row
                if bucket == "today" and day != now.date():
                    reason = "Hoy ya está completo: pasa a mañana."
            else:
                reason = "No quedan huecos libres en la parrilla."
        if chosen and bucket == "urgent":
            day_map(brand, chosen.date())[_nearest_slot(chosen)] = row
        db.exec_("UPDATE candidates SET planned_at=?,plan_reason=? WHERE id=? AND plan_locked=0",
                 (chosen.isoformat(timespec="minutes") if chosen else None, reason or None, row["id"]))
    return get_schedule()
