import sqlite3, json
from .config import DB_PATH

def conn():
    c=sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init_db():
    c=conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sources(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'rss',
      priority INTEGER NOT NULL DEFAULT 50, active INTEGER NOT NULL DEFAULT 1, official INTEGER NOT NULL DEFAULT 0,
      local_scope INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT, last_success_at TEXT,
      last_error TEXT, items_seen INTEGER NOT NULL DEFAULT 0, items_added INTEGER NOT NULL DEFAULT 0,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id INTEGER, source_name TEXT, title TEXT NOT NULL, url TEXT, published_at TEXT,
      excerpt TEXT, raw_text TEXT, research_json TEXT, score INTEGER DEFAULT 0, relevance TEXT DEFAULT 'pending',
      status TEXT DEFAULT 'new', section TEXT, reason TEXT,
      editorial_priority TEXT NOT NULL DEFAULT 'undecided', planned_at TEXT,
      plan_locked INTEGER NOT NULL DEFAULT 0, plan_reason TEXT, local_angle TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(url)
    );
    CREATE TABLE IF NOT EXISTS articles(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_id INTEGER UNIQUE, section TEXT, headline TEXT, subtitle TEXT, body TEXT,
      social_text TEXT, graphic_summary TEXT, research_notes TEXT, sources_json TEXT,
      image_url TEXT, image_source TEXT, image_license TEXT, image_local TEXT, image_candidates_json TEXT,
      ai_image_suggestion TEXT, carousel_suitable INTEGER NOT NULL DEFAULT 0, carousel_reason TEXT,
      carousel_json TEXT, template_id INTEGER, render_path TEXT, workflow TEXT DEFAULT 'normal',
      status TEXT DEFAULT 'draft', publish_url TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS templates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, path TEXT NOT NULL, format TEXT DEFAULT '4:5', active INTEGER DEFAULT 1,
      slide_map_json TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS activity(
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS candidates_created_idx ON candidates(created_at);
    CREATE INDEX IF NOT EXISTS candidates_status_score_idx ON candidates(status,score);
    CREATE TABLE IF NOT EXISTS canva_oauth(
      id INTEGER PRIMARY KEY CHECK(id=1), access_token TEXT NOT NULL,
      refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS canva_oauth_state(
      state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS canva_designs(
      article_id INTEGER PRIMARY KEY, design_id TEXT NOT NULL, url TEXT NOT NULL,
      exported INTEGER NOT NULL DEFAULT 0, content_hash TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS carousel_designs(
      article_id INTEGER NOT NULL, slide_index INTEGER NOT NULL,
      design_id TEXT NOT NULL, url TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0,
      content_hash TEXT, image_url TEXT, image_source TEXT, image_license TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(article_id,slide_index)
    );
    ''')
    # Railway keeps the SQLite volume between deployments.
    existing={r[1] for r in c.execute('PRAGMA table_info(sources)')}
    for name,ddl in {
        'local_scope':'INTEGER NOT NULL DEFAULT 0', 'last_checked_at':'TEXT',
        'last_success_at':'TEXT', 'last_error':'TEXT',
        'items_seen':'INTEGER NOT NULL DEFAULT 0', 'items_added':'INTEGER NOT NULL DEFAULT 0'
    }.items():
        if name not in existing: c.execute(f'ALTER TABLE sources ADD COLUMN {name} {ddl}')
    if 'research_json' not in {r[1] for r in c.execute('PRAGMA table_info(candidates)')}:
        c.execute('ALTER TABLE candidates ADD COLUMN research_json TEXT')
    existing_candidates={r[1] for r in c.execute('PRAGMA table_info(candidates)')}
    for name,ddl in {
        'editorial_priority':"TEXT NOT NULL DEFAULT 'undecided'",
        'planned_at':'TEXT',
        'plan_locked':'INTEGER NOT NULL DEFAULT 0',
        'plan_reason':'TEXT',
        'local_angle':'TEXT',
        'work_state':'TEXT',
        'work_step':'TEXT',
        'work_error':'TEXT',
        'work_updated_at':'TEXT',
        'outlet':'TEXT',
        'scope':'TEXT',
        'social_type':'TEXT'
    }.items():
        if name not in existing_candidates: c.execute(f'ALTER TABLE candidates ADD COLUMN {name} {ddl}')
    existing_articles={r[1] for r in c.execute('PRAGMA table_info(articles)')}
    for name,ddl in {
        'carousel_suitable':'INTEGER NOT NULL DEFAULT 0',
        'carousel_reason':'TEXT',
        'carousel_json':'TEXT',
        'headline_options_json':'TEXT',
        'missing_data_json':'TEXT',
        'ai_provider':'TEXT',
        'image_kind':'TEXT',
        'image_author':'TEXT',
        'image_headline':'TEXT',
        'focus':'TEXT'
    }.items():
        if name not in existing_articles: c.execute(f'ALTER TABLE articles ADD COLUMN {name} {ddl}')
    if 'content_hash' not in {r[1] for r in c.execute('PRAGMA table_info(canva_designs)')}:
        c.execute('ALTER TABLE canva_designs ADD COLUMN content_hash TEXT')
    # Earlier releases could replace a Canva PNG on disk with a PPTX render.
    # Require a fresh Canva design for legacy records before exposing an image.
    c.execute('''UPDATE articles SET render_path=NULL,
                 status=CASE WHEN status='approved' THEN 'review_ready' ELSE status END
                 WHERE id IN (SELECT article_id FROM canva_designs WHERE content_hash IS NULL AND exported=1)''')
    c.execute('UPDATE canva_designs SET exported=0 WHERE content_hash IS NULL AND exported=1')
    c.execute("UPDATE candidates SET work_state='error',work_error='El servidor se reinició mientras trabajaba. Pulsa Reintentar.' WHERE work_state='working'")
    c.execute("UPDATE candidates SET work_state=NULL WHERE work_state='queued'")
    from urllib.parse import quote
    def news(query):
        return 'https://news.google.com/rss/search?q='+quote(query)+'&hl=es&gl=ES&ceid=ES:es'
    defaults=[
      ('Ayuntamiento de La Línea','https://lalinea.es/feed/','rss',100,1,1),
      ('Google News · La Línea exacta','https://news.google.com/rss/search?q=%22La+L%C3%ADnea+de+la+Concepci%C3%B3n%22&hl=es&gl=ES&ceid=ES:es','rss',70,0,0),
      ('Tablón municipal de edictos','https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true','html',96,1,1),
      ('Gobierno de Gibraltar · prensa','https://www.gibraltar.gov.gi/press-releases','html',57,1,0),
      ('APBA · noticias','https://www.apba.es/noticias','html',75,1,0),
      ('Europa Sur · La Línea','https://www.europasur.es/lalinea/','html',88,0,1),
      ('Europa Sur · RSS','https://www.europasur.es/rss/','rss',78,0,0),
      ('BOP Cádiz · anuncios','https://bopcadiz.es/boletin/','bop',94,1,0),
      ('BOE · contratación','https://www.boe.es/rss/boe.php?s=5A','rss',82,1,0),
      ('BOE · otros anuncios','https://www.boe.es/rss/boe.php?s=5B','rss',78,1,0),
      ('Noticias · prensa comarcal',news('"La Línea de la Concepción" (site:diarioarea.com OR site:8directo.com OR site:cadenaser.com) when:3d'),'rss',82,0,0),
      ('Noticias · televisión y agencias',news('"La Línea de la Concepción" (site:canalsur.es OR site:europapress.es OR site:rtva.es) when:3d'),'rss',69,0,0),
      ('Contratos · plataforma estatal',news('"Ayuntamiento de la Línea de la Concepción" site:contrataciondelestado.es when:7d'),'rss',92,1,0),
      ('Licitaciones municipales · Gobierto','https://contratos.gobierto.es/adjudicadores/alcaldia-del-ayuntamiento-de-la-linea-de-la-concepcion','procurement',89,0,1),
      ('Edictos · sede municipal indexada',news('"La Línea de la Concepción" site:sedeelectronica.lalinea.es/edictos/ when:7d'),'rss',84,1,0),
      ('Anuncios · BOJA y BOP indexados',news('"La Línea de la Concepción" (site:juntadeandalucia.es/boja/ OR site:bopcadiz.es) when:7d'),'rss',80,1,0),
      ('Facebook · publicaciones indexadas',news('"La Línea de la Concepción" site:facebook.com when:3d'),'rss',55,0,0),
      ('Instagram · publicaciones indexadas',news('"La Línea de la Concepción" site:instagram.com when:3d'),'rss',55,0,0),
      ('Facebook · Ayuntamiento', 'https://www.facebook.com/aytolalinea','social',74,1,0),
      ('Instagram · Turismo local','https://www.instagram.com/oficinaturismolalinea/','social',67,1,0),
    ]
    for n,u,k,p,o,scope in defaults:
        c.execute('INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope) VALUES(?,?,?,?,?,?)',(n,u,k,p,o,scope))
    # Existing default rows retain the same URL but need accurate locality rules.
    c.execute("UPDATE sources SET local_scope=1 WHERE url='https://lalinea.es/feed/'")
    c.execute("UPDATE sources SET local_scope=1 WHERE url LIKE 'https://www.sedeelectronica.lalinea.es/edictos/%'")
    c.commit(); c.close()

def rows(sql,args=()):
    c=conn(); r=[dict(x) for x in c.execute(sql,args).fetchall()]; c.close(); return r

def row(sql,args=()):
    c=conn(); r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None

def exec_(sql,args=()):
    c=conn(); cur=c.execute(sql,args); c.commit(); rid=cur.lastrowid; c.close(); return rid

def log(kind,msg):
    exec_('INSERT INTO activity(kind,message) VALUES(?,?)',(kind,msg))
