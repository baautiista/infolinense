"""Efemérides del Campo de Gibraltar: fechas para piezas de «tal día como hoy» y aniversarios redondos.

Las que vienen de serie están contrastadas; las que se añaden a mano o propone la IA quedan como «sin verificar»
hasta que el editor las marca. Se gestionan en Ajustes → Efemérides.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db

MADRID = ZoneInfo('Europe/Madrid')

SEED = [
    (1, 16, 1906, 'Se inaugura la Conferencia de Algeciras', 'Reunión internacional sobre Marruecos celebrada en el Ayuntamiento de Algeciras (16 de enero–7 de abril de 1906).', 'Algeciras'),
    (4, 7, 1906, 'Se firma el Acta de Algeciras', 'Cierre de la Conferencia de Algeciras sobre Marruecos.', 'Algeciras'),
    (2, 5, 1985, 'Apertura total de la Verja de Gibraltar', 'Se abre la frontera al tráfico de personas y vehículos tras el cierre de 1969.', 'La Línea'),
    (2, 25, 2014, 'Muere Paco de Lucía', 'El guitarrista algecireño falleció en Playa del Carmen (México).', 'Algeciras'),
    (6, 8, 1969, 'Cierre de la Verja de Gibraltar', 'El Gobierno de Franco cierra la frontera con Gibraltar; permanecerá cerrada hasta 1982 (peatones) y 1985 (total).', 'La Línea'),
    (7, 6, 1801, 'Primera batalla de Algeciras', 'Combate naval en la bahía entre la escuadra británica y la franco-española.', 'Algeciras'),
    (7, 13, 1713, 'Firma del Tratado de Utrecht', 'España cede Gibraltar a Gran Bretaña (artículo X).', 'Campo de Gibraltar'),
    (7, 20, 1870, 'La Línea se constituye como municipio', 'La Línea de la Concepción se segrega de San Roque y nace como ayuntamiento propio.', 'La Línea,San Roque'),
    (8, 4, 1704, 'Toma de Gibraltar por la flota anglo-holandesa', 'Origen del éxodo de la población gibraltareña hacia San Roque, Algeciras y Los Barrios.', 'Campo de Gibraltar'),
    (12, 15, 1982, 'Reapertura peatonal de la Verja', 'Primeros pasos de peatones tras trece años de cierre.', 'La Línea'),
    (12, 21, 1947, 'Nace Paco de Lucía', 'Francisco Sánchez Gómez nace en Algeciras.', 'Algeciras'),
    (12, 31, 2020, 'Acuerdo de Nochevieja sobre Gibraltar', 'España y Reino Unido alcanzan el principio de acuerdo para la relación de Gibraltar con la UE tras el Brexit.', 'La Línea,Campo de Gibraltar'),
]


def seed():
    for m, d, y, t, n, towns in SEED:
        db.exec_('INSERT OR IGNORE INTO efemerides(month,day,year,title,note,towns,verified) VALUES(?,?,?,?,?,?,1)', (m, d, y, t, n, towns))


def today():
    return datetime.now(MADRID).date()


def upcoming(days=14, start=None):
    """Efemérides de los próximos días, con los años que se cumplen; las redondas primero en importancia."""
    start = start or today()
    out = []
    for e in db.rows('SELECT * FROM efemerides WHERE active=1'):
        for year in (start.year, start.year + 1):
            try:
                when = date(year, e['month'], e['day'])
            except ValueError:
                continue
            delta = (when - start).days
            if 0 <= delta <= days:
                years = (year - e['year']) if e.get('year') else None
                round_ = bool(years and (years % 25 == 0 or years % 10 == 0 or years in (5, 15)))
                out.append({**e, 'date': when.isoformat(), 'in_days': delta, 'years': years, 'round': round_})
                break
    out.sort(key=lambda x: (x['in_days'], not x['round']))
    return out


def as_candidates(days=10):
    """Se proponen en el radar las efemérides cercanas (las redondas con más puntuación)."""
    from . import sources
    added = []
    for e in upcoming(days):
        label = '%s años' % e['years'] if e.get('years') else 'aniversario'
        title = 'Efeméride (%s, %s): %s' % (_fmt(e['date']), label, e['title'])
        url = 'efemeride://%s/%s' % (e['id'], e['date'][:4])
        published = datetime.now(MADRID).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
        cid = sources.add_candidate({'title': title, 'url': url, 'excerpt': (e.get('note') or '') + ('' if e.get('verified') else ' [Sin verificar]'),
                                     'published_at': published},
                                    {'id': None, 'name': 'Efemérides', 'block': 'Efemérides', 'priority': 80 if e['round'] else 60,
                                     'official': 0, 'kind': 'efemeride'}, force=True)
        if cid:
            added.append(cid)
    return added


_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _fmt(iso):
    d = date.fromisoformat(iso[:10])
    return '%s de %s' % (d.day, _MESES[d.month - 1])
