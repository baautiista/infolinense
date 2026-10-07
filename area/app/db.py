"""SQLite de Área Desk (en Railway, en el volumen /data)."""
import sqlite3
from urllib.parse import quote

from .config import DB_PATH


def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def rows(sql, args=()):
    c = conn()
    try:
        return [dict(x) for x in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def row(sql, args=()):
    c = conn()
    try:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def exec_(sql, args=()):
    c = conn()
    try:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def log(kind, msg):
    exec_('INSERT INTO activity(kind,message) VALUES(?,?)', (kind, str(msg)[:1000]))


def setting(key, default=''):
    r = row('SELECT value FROM settings WHERE key=?', (key,))
    return r['value'] if r and r['value'] is not None else default


def set_setting(key, value):
    exec_('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, value))


def gnews(query):
    return 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=es&gl=ES&ceid=ES:es'


TOWNS_Q = '(Algeciras OR "La Línea" OR "San Roque" OR "Los Barrios" OR Tarifa OR "Jimena de la Frontera" OR "Castellar de la Frontera" OR "Campo de Gibraltar")'

# (nombre, url, tipo, bloque, prioridad, oficial)
# Bloques: Exclusivas · Obras y urbanismo · Agenda y cultura · Instituciones · Nacionales adaptables · Medios
DEFAULT_SOURCES = [
    # --- LICITACIONES: todos los municipios, empresas municipales, Mancomunidad, puerto, Junta y Estado ---
    # La Plataforma de Contratación recoge por ley todos los perfiles del contratante (y, en «agregadas», la de la Junta).
    ('Plataforma de Contratación · perfiles', 'https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom', 'placsp', 'Licitaciones', 99, 1),
    ('Plataforma de Contratación · Junta y agregadas', 'https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores.atom', 'placsp', 'Licitaciones', 98, 1),
    ('Plataforma de Contratación · contratos menores', 'https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1143/contratosMenoresPerfilesContratantes.atom', 'placsp', 'Licitaciones', 92, 1),
    ('Gobierto · Ayuntamiento de Algeciras', 'https://contratos.gobierto.es/adjudicadores/ayuntamiento-de-algeciras', 'gobierto', 'Licitaciones', 95, 1),
    ('Gobierto · Ayuntamiento de La Línea', 'https://contratos.gobierto.es/adjudicadores/alcaldia-del-ayuntamiento-de-la-linea-de-la-concepcion', 'gobierto', 'Licitaciones', 95, 1),
    ('Gobierto · Ayuntamiento de San Roque', 'https://contratos.gobierto.es/adjudicadores/ayuntamiento-de-san-roque', 'gobierto', 'Licitaciones', 90, 1),
    ('Licitaciones en prensa · Algeciras', gnews('(Algeciras OR APBA OR "puerto de Algeciras" OR Emalgesa) (licitación OR licita OR adjudica OR adjudicación OR pliego) when:7d'), 'rss', 'Licitaciones', 86, 0),
    ('Licitaciones en prensa · La Línea', gnews('"La Línea" (licitación OR licita OR adjudica OR adjudicación OR pliego) when:7d'), 'rss', 'Licitaciones', 86, 0),
    ('Licitaciones en prensa · San Roque y Los Barrios', gnews('("San Roque" OR "Los Barrios" OR Sotogrande OR Palmones) (licitación OR licita OR adjudica OR adjudicación OR pliego) when:7d'), 'rss', 'Licitaciones', 86, 0),
    ('Licitaciones en prensa · Tarifa, Jimena, Castellar y Tesorillo', gnews('(Tarifa OR Jimena OR Castellar OR Tesorillo) (licitación OR licita OR adjudica OR adjudicación OR pliego) ayuntamiento when:7d'), 'rss', 'Licitaciones', 84, 0),
    ('Licitaciones en prensa · Mancomunidad, Junta y Estado', gnews('("Campo de Gibraltar" OR Mancomunidad OR Arcgisa) (licitación OR licita OR adjudica OR adjudicación OR pliego) when:7d'), 'rss', 'Licitaciones', 84, 0),
    # --- Boletines y edictos ---
    ('BOP Cádiz · anuncios', 'https://bopcadiz.es/boletin/', 'bop', 'Exclusivas', 96, 1),
    ('BOE · contratación', 'https://www.boe.es/rss/boe.php?s=5A', 'rss', 'Exclusivas', 90, 1),
    ('BOE · otros anuncios', 'https://www.boe.es/rss/boe.php?s=5B', 'rss', 'Exclusivas', 85, 1),
    ('Tablón de edictos · La Línea', 'https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true', 'edictos', 'Exclusivas', 92, 1),
    ('Presupuestos y plenos', gnews(TOWNS_Q + ' (presupuesto OR "modificación presupuestaria" OR pleno OR "junta de gobierno" OR subvención OR "fondos europeos") when:3d'), 'rss', 'Exclusivas', 85, 0),
    ('BOJA y BOP indexados', gnews(TOWNS_Q + ' (site:juntadeandalucia.es/boja OR site:bopcadiz.es) when:10d'), 'rss', 'Exclusivas', 88, 1),
    # --- Instituciones ---
    ('Ayuntamiento de La Línea', 'https://lalinea.es/feed/', 'rss', 'Instituciones', 90, 1),
    ('Ayuntamiento de Los Barrios', 'https://www.losbarrios.es/feed/', 'rss', 'Instituciones', 90, 1),
    ('Ayuntamiento de Tarifa', 'https://www.aytotarifa.com/feed/', 'rss', 'Instituciones', 90, 1),
    ('Mancomunidad del Campo de Gibraltar', 'https://www.mancomunidadcg.es/feed/', 'rss', 'Instituciones', 92, 1),
    ('Ayuntamiento de Algeciras', gnews('site:algeciras.es when:3d'), 'rss', 'Instituciones', 90, 1),
    ('Ayuntamiento de San Roque', gnews('site:sanroque.es when:3d'), 'rss', 'Instituciones', 90, 1),
    ('Ayuntamiento de Jimena', gnews('(site:jimenadelafrontera.es OR "Ayuntamiento de Jimena") when:4d'), 'rss', 'Instituciones', 85, 1),
    ('Ayuntamiento de Castellar', gnews('(site:castellardelafrontera.es OR "Ayuntamiento de Castellar") when:4d'), 'rss', 'Instituciones', 85, 1),
    ('Puerto · APBA', 'https://www.apba.es/noticias', 'html', 'Instituciones', 92, 1),
    ('Puerto · noticias indexadas', gnews('("Puerto de Algeciras" OR APBA OR "Puerto de Tarifa" OR "Puerto de La Línea" OR "Autoridad Portuaria de la Bahía de Algeciras") when:3d'), 'rss', 'Instituciones', 86, 0),
    ('Junta de Andalucía · Campo de Gibraltar', gnews(TOWNS_Q + ' site:juntadeandalucia.es when:7d'), 'rss', 'Instituciones', 88, 1),
    ('Gobierno de España · Campo de Gibraltar', gnews(TOWNS_Q + ' (site:lamoncloa.gob.es OR site:transportes.gob.es OR site:mpt.gob.es OR site:interior.gob.es OR site:hacienda.gob.es OR site:defensa.gob.es) when:7d'), 'rss', 'Instituciones', 86, 1),
    ('Diputación de Cádiz · Campo de Gibraltar', gnews(TOWNS_Q + ' site:dipucadiz.es when:7d'), 'rss', 'Instituciones', 80, 1),
    ('Gibraltar · frontera, obras y acuerdos', gnews('Gibraltar (frontera OR tratado OR acuerdo OR verja OR Schengen OR relleno OR obras OR proyecto) when:2d'), 'rss', 'Instituciones', 78, 0),
    # --- Obras y urbanismo ---
    ('Obras y urbanismo', gnews(TOWNS_Q + ' (obras OR urbanismo OR proyecto OR PGOU OR "licencia de obras" OR viviendas OR rehabilitación OR "nuevo edificio" OR "así será" OR "así quedará") when:3d'), 'rss', 'Obras y urbanismo', 88, 0),
    ('Infraestructuras comarcales', gnews('("Algeciras-Bobadilla" OR "A-7" OR "variante" OR "tren Algeciras" OR "autovía del Campo de Gibraltar" OR "A-48" OR "nudo de" OR desdoble OR "hospital comarcal") (Algeciras OR "Campo de Gibraltar" OR "San Roque" OR "La Línea") when:4d'), 'rss', 'Obras y urbanismo', 86, 0),
    # --- Agenda y cultura ---
    ('Agenda, conciertos y programación', gnews(TOWNS_Q + ' (concierto OR festival OR programación OR "semana cultural" OR feria OR exposición OR teatro OR "fin de semana" OR cartel) when:4d'), 'rss', 'Agenda y cultura', 80, 0),
    # --- Nacionales para adaptar al Campo de Gibraltar ---
    ('Nacionales · datos por municipios', gnews('("por municipios" OR "mapa de la renta" OR "municipios de España" OR "municipio a municipio" OR "ranking de municipios" OR "en tu municipio" OR "consulta tu municipio") when:2d'), 'rss', 'Nacionales adaptables', 82, 0),
    ('Nacionales · INE y estadísticas', gnews('(INE OR "Agencia Tributaria" OR "Seguridad Social" OR SEPE OR Idealista OR Fotocasa) (municipios OR provincias OR "por ciudades") when:2d'), 'rss', 'Nacionales adaptables', 76, 0),
    ('Nacionales · Campo de Gibraltar en medios nacionales', gnews(TOWNS_Q + ' (site:elpais.com OR site:elmundo.es OR site:abc.es OR site:rtve.es OR site:elconfidencial.com OR site:eldiario.es OR site:elespanol.com OR site:europapress.es OR site:efe.com) when:2d'), 'rss', 'Nacionales adaptables', 74, 0),
]

# Medios para saber qué está ya contado (no generan propuestas; sirven para medir la exclusiva).
DEFAULT_PRESS = [
    ('Diario Área', 'https://www.diarioarea.com/feed/', 'area'),
    ('Diario Área (buscador)', gnews('site:diarioarea.com when:10d'), 'area'),
    ('Europa Sur', 'https://www.europasur.es/rss/', 'competitor'),
    ('Europa Sur (buscador)', gnews('site:europasur.es when:5d'), 'competitor'),
    ('8Directo', gnews('site:8directo.com when:5d'), 'competitor'),
    ('Cadena SER Campo de Gibraltar', gnews('site:cadenaser.com ' + TOWNS_Q + ' when:5d'), 'competitor'),
    ('Algeciras al Minuto / Algeciras.es medios', gnews('(site:algecirasalminuto.es OR site:lalineaalminuto.es OR site:sanroquealminuto.es) when:5d'), 'competitor'),
    ('Onda Cero / COPE / Canal Sur Campo', gnews(TOWNS_Q + ' (site:ondacero.es OR site:cope.es OR site:canalsur.es) when:5d'), 'competitor'),
]


def init_db():
    c = conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sources(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'rss',
      block TEXT NOT NULL DEFAULT 'Instituciones', priority INTEGER NOT NULL DEFAULT 60, active INTEGER NOT NULL DEFAULT 1,
      official INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT, last_success_at TEXT, last_error TEXT,
      items_seen INTEGER NOT NULL DEFAULT 0, items_added INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS press_sources(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, role TEXT NOT NULL DEFAULT 'competitor',
      active INTEGER NOT NULL DEFAULT 1, last_checked_at TEXT, last_error TEXT, items_seen INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS press(
      id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, outlet TEXT, title TEXT NOT NULL, url TEXT UNIQUE,
      excerpt TEXT, published_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS press_role_idx ON press(role, published_at);
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, source_name TEXT, outlet TEXT, title TEXT NOT NULL, url TEXT UNIQUE,
      published_at TEXT, excerpt TEXT, image_hint TEXT, block TEXT, tag TEXT, towns TEXT, score INTEGER DEFAULT 0,
      exclusive INTEGER NOT NULL DEFAULT 0, competitors_json TEXT, area_state TEXT NOT NULL DEFAULT '', area_url TEXT, area_title TEXT,
      organism TEXT, amount TEXT, deadline TEXT, docs_json TEXT, local_angle TEXT, why TEXT,
      status TEXT NOT NULL DEFAULT 'new', priority TEXT NOT NULL DEFAULT 'undecided', merged_into INTEGER,
      work_state TEXT, work_error TEXT, work_updated_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS cand_status_idx ON candidates(status, priority);
    CREATE TABLE IF NOT EXISTS candidate_links(
      id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, source_name TEXT, outlet TEXT, url TEXT UNIQUE,
      title TEXT, excerpt TEXT, published_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS links_cand_idx ON candidate_links(candidate_id);
    CREATE TABLE IF NOT EXISTS articles(
      id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER UNIQUE, status TEXT NOT NULL DEFAULT 'draft',
      section TEXT, town TEXT, focus TEXT, headline TEXT, entradilla TEXT, body TEXT, instagram_copy TEXT,
      headline_options_json TEXT, slides_json TEXT, render_json TEXT, photo_query TEXT, sources_json TEXT,
      area_note TEXT, provider TEXT, canva_design_id TEXT, canva_url TEXT, canva_error TEXT, published_url TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS media(
      id INTEGER PRIMARY KEY AUTOINCREMENT, article_id INTEGER NOT NULL, kind TEXT NOT NULL, url TEXT, source TEXT,
      local_path TEXT, width INTEGER, height INTEGER, caption TEXT, slide INTEGER, selected INTEGER NOT NULL DEFAULT 0,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS media_article_idx ON media(article_id);
    CREATE TABLE IF NOT EXISTS efemerides(
      id INTEGER PRIMARY KEY AUTOINCREMENT, month INTEGER NOT NULL, day INTEGER NOT NULL, year INTEGER, title TEXT NOT NULL,
      note TEXT, towns TEXT, verified INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, UNIQUE(month, day, title));
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS canva_oauth(id INTEGER PRIMARY KEY CHECK(id=1), access_token TEXT NOT NULL, refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS canva_oauth_state(state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires_at INTEGER NOT NULL);
    ''')
    have = {r[1] for r in c.execute('PRAGMA table_info(candidates)')}
    for name, ddl in (('tstatus', "TEXT NOT NULL DEFAULT ''"), ('amount_value', 'REAL NOT NULL DEFAULT 0'), ('winner', 'TEXT')):
        if name not in have:
            c.execute('ALTER TABLE candidates ADD COLUMN %s %s' % (name, ddl))
    c.execute("CREATE INDEX IF NOT EXISTS cand_block_idx ON candidates(block, status)")
    # Las licitaciones pasan a su propio bloque (1.1)
    c.execute("UPDATE sources SET block='Licitaciones' WHERE kind IN ('placsp','gobierto') OR name LIKE 'Licitaciones%'")
    c.execute("UPDATE candidates SET block='Licitaciones' WHERE tag='licitacion' AND block IN ('Exclusivas','Instituciones','Obras y urbanismo')")
    for n, u, k, b, p, o in DEFAULT_SOURCES:
        c.execute('INSERT OR IGNORE INTO sources(name,url,kind,block,priority,official) VALUES(?,?,?,?,?,?)', (n, u, k, b, p, o))
    for n, u, r in DEFAULT_PRESS:
        c.execute('INSERT OR IGNORE INTO press_sources(name,url,role) VALUES(?,?,?)', (n, u, r))
    c.execute("UPDATE candidates SET work_state='error',work_error='El servidor se reinició mientras redactaba. Pulsa Reintentar.' WHERE work_state='working'")
    c.execute("UPDATE candidates SET work_state=NULL WHERE work_state='queued'")
    c.commit()
    c.close()
    from . import efemerides
    efemerides.seed()
