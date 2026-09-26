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
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id INTEGER, source_name TEXT, title TEXT NOT NULL, url TEXT, published_at TEXT,
      excerpt TEXT, raw_text TEXT, score INTEGER DEFAULT 0, relevance TEXT DEFAULT 'pending',
      status TEXT DEFAULT 'new', section TEXT, reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(url)
    );
    CREATE TABLE IF NOT EXISTS articles(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      candidate_id INTEGER UNIQUE, section TEXT, headline TEXT, subtitle TEXT, body TEXT,
      social_text TEXT, graphic_summary TEXT, research_notes TEXT, sources_json TEXT,
      image_url TEXT, image_source TEXT, image_license TEXT, image_local TEXT, image_candidates_json TEXT,
      ai_image_suggestion TEXT, template_id INTEGER, render_path TEXT, workflow TEXT DEFAULT 'normal',
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
    CREATE TABLE IF NOT EXISTS canva_oauth(
      id INTEGER PRIMARY KEY CHECK(id=1), access_token TEXT NOT NULL,
      refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS canva_oauth_state(
      state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS canva_designs(
      article_id INTEGER PRIMARY KEY, design_id TEXT NOT NULL, url TEXT NOT NULL,
      exported INTEGER NOT NULL DEFAULT 0, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    defaults=[
      ('Ayuntamiento de La Línea','https://lalinea.es/feed/','rss',100,1),
      ('Google News · La Línea exacta','https://news.google.com/rss/search?q=%22La+L%C3%ADnea+de+la+Concepci%C3%B3n%22&hl=es&gl=ES&ceid=ES:es','rss',70,0),
      ('Tablón de Edictos','https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true','html',95,1),
      ('Gobierno de Gibraltar · prensa','https://www.gibraltar.gov.gi/press-releases','html',70,1),
      ('APBA · noticias','https://www.apba.es/noticias','html',75,1)
    ]
    for n,u,k,p,o in defaults:
        c.execute('INSERT OR IGNORE INTO sources(name,url,kind,priority,official) VALUES(?,?,?,?,?)',(n,u,k,p,o))
    if not c.execute('SELECT 1 FROM templates LIMIT 1').fetchone():
        slide_map={'URBANISMO':1,'CIUDAD':2,'GIBRALTAR':3,'SUCESOS':4,'CULTURA':5,'DEPORTES':6,'COMERCIO':7,'MEDIO AMBIENTE':8,'POLÍTICA':9,'SOCIEDAD':10,'PATRIMONIO':11,'AGENDA':12}
        c.execute('INSERT INTO templates(name,path,format,slide_map_json) VALUES(?,?,?,?)',('Noticia principal v1','templates/plantilla_1.pptx','4:5',json.dumps(slide_map,ensure_ascii=False)))
    c.commit(); c.close()

def rows(sql,args=()):
    c=conn(); r=[dict(x) for x in c.execute(sql,args).fetchall()]; c.close(); return r

def row(sql,args=()):
    c=conn(); r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None

def exec_(sql,args=()):
    c=conn(); cur=c.execute(sql,args); c.commit(); rid=cur.lastrowid; c.close(); return rid

def log(kind,msg):
    exec_('INSERT INTO activity(kind,message) VALUES(?,?)',(kind,msg))
