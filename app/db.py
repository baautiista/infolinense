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
      candidate_id INTEGER UNIQUE, section TEXT, headline TEXT, headline_size TEXT NOT NULL DEFAULT 'auto', subtitle TEXT, body TEXT,
      photo_zoom REAL NOT NULL DEFAULT 1, photo_x REAL NOT NULL DEFAULT 50, photo_y REAL NOT NULL DEFAULT 50,
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
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
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
        'items_seen':'INTEGER NOT NULL DEFAULT 0', 'items_added':'INTEGER NOT NULL DEFAULT 0',
        'brand':"TEXT NOT NULL DEFAULT 'infolinense'"
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
        'social_type':'TEXT',
        'image_hint':'TEXT',
        'merged_into':'INTEGER',
        'plan_day':'TEXT',
        'manual_photo':'TEXT',
        'brand':"TEXT NOT NULL DEFAULT 'infolinense'"
    }.items():
        if name not in existing_candidates: c.execute(f'ALTER TABLE candidates ADD COLUMN {name} {ddl}')
    existing_articles={r[1] for r in c.execute('PRAGMA table_info(articles)')}
    for name,ddl in {
        'carousel_suitable':'INTEGER NOT NULL DEFAULT 0',
        'scheduled_at':'TEXT',
        'section_label':'TEXT',
        'time_ref':'TEXT',
        'canva_error':'TEXT',
        'hidden':'INTEGER DEFAULT 0',
        'brand':"TEXT NOT NULL DEFAULT 'infolinense'",
        'scheduled_networks':'TEXT',
        'schedule_error':'TEXT',
        'carousel_reason':'TEXT',
        'carousel_json':'TEXT',
        'headline_options_json':'TEXT',
        'missing_data_json':'TEXT',
        'ai_provider':'TEXT',
        'image_kind':'TEXT',
        'image_author':'TEXT',
        'image_headline':'TEXT',
        'focus':'TEXT',
        'photo_query':'TEXT',
        'headline_size':"TEXT NOT NULL DEFAULT 'auto'",
        'photo_zoom':'REAL NOT NULL DEFAULT 1',
        'photo_x':'REAL NOT NULL DEFAULT 50',
        'photo_y':'REAL NOT NULL DEFAULT 50'
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
      ('Tablón municipal de edictos','https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true','edictos',99,1,1),
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
      ('Medios nacionales',news('"La Línea de la Concepción" (site:elpais.com OR site:elmundo.es OR site:abc.es OR site:rtve.es OR site:lavanguardia.com OR site:elconfidencial.com OR site:20minutos.es OR site:eldiario.es OR site:elespanol.com OR site:larazon.es OR site:antena3.com OR site:telecinco.es OR site:cope.es OR site:ondacero.es OR site:efe.com OR site:lasexta.com) when:2d'),'rss',80,0,0),
      ('Nacionales adaptables · Gibraltar y frontera',news('Gibraltar (frontera OR tratado OR acuerdo OR "trabajadores transfronterizos" OR verja OR Schengen) when:2d'),'rss',78,0,0),
      ('Gibraltar · rellenos, obras, eventos y elecciones',news('Gibraltar (relleno OR obras OR construcción OR proyecto OR festival OR concierto OR elecciones OR "Día Nacional") when:3d'),'rss',70,0,0),
      ('Nacionales adaptables · Campo de Gibraltar',news('"Campo de Gibraltar" when:2d'),'rss',72,0,0),
      ('Licitaciones · BOP y plataforma estatal',news('("La Línea de la Concepción" OR "Ayuntamiento de La Línea") (licitación OR adjudicación OR contrato OR obras) when:7d'),'rss',95,1,0),
      ('Facebook · publicaciones indexadas',news('"La Línea de la Concepción" site:facebook.com when:3d'),'rss',55,0,0),
      ('Instagram · publicaciones indexadas',news('"La Línea de la Concepción" site:instagram.com when:3d'),'rss',55,0,0),
      ('Facebook · Ayuntamiento', 'https://www.facebook.com/aytolalinea','social',74,1,0),
      ('Instagram · Turismo local','https://www.instagram.com/oficinaturismolalinea/','social',67,1,0),
    ]
    for n,u,k,p,o,scope in defaults:
        c.execute('INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope) VALUES(?,?,?,?,?,?)',(n,u,k,p,o,scope))
    # Existing default rows retain the same URL but need accurate locality rules.
    c.execute("UPDATE sources SET local_scope=1 WHERE url='https://lalinea.es/feed/'")
    c.execute("UPDATE sources SET local_scope=1,kind='edictos',priority=99 WHERE url LIKE 'https://www.sedeelectronica.lalinea.es/edictos/%'")
    c.execute("UPDATE sources SET priority=97 WHERE kind='procurement'")
    # Otras fuentes de la misma noticia (se unen en una sola tarjeta)
    c.execute('''CREATE TABLE IF NOT EXISTS candidate_links(id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL,
                 source_name TEXT, outlet TEXT, url TEXT UNIQUE, title TEXT, excerpt TEXT, published_at TEXT,
                 created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_links_cand ON candidate_links(candidate_id)')
    # Fuentes de El Cofrade Linense y Carnavalinense (5.8)
    cofrade=[('La Línea Cofrade','https://www.lalineacofrade.com/','html'),
      ('Entrada Triunfal y Alegría','https://www.facebook.com/hermandad.entradatriunfal','social'),
      ('Flagelación y Estrella','https://www.facebook.com/flagelacion.estrella.16','social'),
      ('Esperanza y Concepción (Silencio)','https://www.facebook.com/SilencioLaLinea','social'),
      ('Penas y Dolores','https://www.facebook.com/profile.php?id=100064501760045','social'),
      ('Oración y Amor','https://www.facebook.com/Hermandaddelaoracionlalinea','social'),
      ('Abandono y Mayor Dolor','https://www.facebook.com/abandonoymayordolor','social'),
      ('Cautivo y Trinidad (Medinaceli)','https://www.facebook.com/profile.php?id=100064805047717','social'),
      ('Perdón y Salud','https://www.facebook.com/sanpedrolalinea','social'),
      ('Almas y Angustias','https://www.facebook.com/almasyangustias','social'),
      ('Gran Poder y Ángeles','https://www.facebook.com/hdadgranpoderlalinea','social'),
      ('Misericordia y Amargura','https://www.facebook.com/SacramentalYRealHermandaddelaAmargura','social'),
      ('Amor y Esperanza','https://www.facebook.com/hermandadamoresperanzalalinea','social'),
      ('Cristo del Mar · Santo Entierro','https://www.facebook.com/cristodelmar.lalinea','social'),
      ('Cofrade · noticias de La Línea',news('"La Línea" (hermandad OR cofradía OR cofrade OR "Semana Santa" OR costaleros OR besamanos) when:3d'),'rss')]
    for n,u,k in cofrade:
        c.execute("INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope,brand) VALUES(?,?,?,?,?,?,?)",
                  (n,u,k,85 if k!='rss' else 75,0,0 if k=='rss' else 1,'cofrade'))
    carnaval=[('Asociación ACALI (La Línea)','https://www.facebook.com/Accalivolao'),
      ('Comparsa Morenopolo (La Línea)','https://www.facebook.com/lolo.lolito.7127'),
      ('Comparsa Los Niños (Los Barrios)','https://www.facebook.com/profile.php?id=61579483787143'),
      ('Comparsa Hermanos Torres (Algeciras)','https://www.facebook.com/TutontinaventureroCarnaval2025'),
      ('Chirigota Los Moriegas (La Línea)','https://www.facebook.com/profile.php?id=61593395201136'),
      ('Chirigota de la Bajadilla (Algeciras)','https://www.facebook.com/lachirigotadelabajadilla2.0'),
      ('Chirigota Bau y Ocaña (La Línea)','https://www.facebook.com/profile.php?id=100037188225698'),
      ('Chirigota de San Roque (San Roque)','https://www.facebook.com/profile.php?id=100085719862408'),
      ('Chirigota de San Roque 2 (San Roque)','https://www.facebook.com/profile.php?id=61573955275998'),
      ('Chirigota del Tini (Algeciras)','https://www.facebook.com/luismartin.galindeztellitu.3')]
    for n,u in carnaval:
        c.execute("INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope,brand) VALUES(?,?,?,?,?,?,?)",
                  (n,u,'social',85 if 'La Línea' in n else 75,0,1,'carnaval'))
    c.execute("INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope,brand) VALUES(?,?,?,?,?,?,?)",
              ('Carnaval · noticias de La Línea',news('"La Línea" (carnaval OR chirigota OR comparsa OR murga OR cuarteto OR agrupación) when:3d'),'rss',75,0,0,'carnaval'))
    # Lista ampliada de fuentes (6.4.5): cada grupo busca solo lo que menciona La Línea en esos medios
    more=[
      ('Plataforma de Contratación · Ayuntamiento de La Línea','https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom','procurement',96,1,1),
      ('Plataforma de Contratación · contratos menores de La Línea','https://contrataciondelestado.es/sindicacion/sindicacion_1143/contratosMenoresPerfilesContratantes.atom','procurement',90,1,1),
      ('Medios nacionales (Público, infoLibre, Vozpópuli, HuffPost, Servimedia, Newtral, Maldita)',news('"La Línea de la Concepción" (site:publico.es OR site:infolibre.es OR site:vozpopuli.com OR site:huffingtonpost.es OR site:servimedia.es OR site:newtral.es OR site:maldita.es) when:3d'),'rss',72,0,0),
      ('Radio y agencias (SER, COPE, Onda Cero, Europa Press, EFE)',news('"La Línea" (site:cadenaser.com OR site:cope.es OR site:ondacero.es OR site:europapress.es OR site:efe.com) when:2d'),'rss',80,0,0),
      ('Prensa andaluza (Grupo Joly, SUR, Ideal, Córdoba, La Voz del Sur…)',news('"La Línea de la Concepción" (site:diariodesevilla.es OR site:malagahoy.es OR site:granadahoy.com OR site:huelvainformacion.es OR site:diariodecadiz.es OR site:diariodejerez.es OR site:diariosur.es OR site:ideal.es OR site:diariocordoba.com OR site:cordopolis.es OR site:lavozdelsur.es OR site:cadizdirecto.com OR site:andaluciainformacion.es OR site:granadadigital.es) when:3d'),'rss',78,0,0),
      ('Canal Sur, ABC Sevilla y Europa Press Andalucía',news('"La Línea de la Concepción" (site:canalsur.es OR site:abc.es/sevilla OR site:europapress.es/andalucia) when:3d'),'rss',76,0,0),
      ('Junta de Andalucía · noticias',news('"La Línea de la Concepción" site:juntadeandalucia.es when:7d'),'rss',84,1,0),
      ('Diputación de Cádiz',news('"La Línea" site:dipucadiz.es when:7d'),'rss',82,1,0),
      ('Mancomunidad del Campo de Gibraltar',news('"La Línea" (site:mancomunidadcg.es OR Mancomunidad "Campo de Gibraltar") when:7d'),'rss',80,1,0),
      ('Prensa del Campo de Gibraltar (Área, 8Directo, HoraSur, Europa Sur, Radio Algeciras, Onda Cero)',news('"La Línea" (site:diarioarea.com OR site:8directo.com OR site:horasur.com OR site:europasur.es OR site:cadenaser.com OR site:ondacero.es) when:2d'),'rss',88,0,0),
      ('Prensa de Gibraltar (Chronicle, GBC, YGTV, Panorama, Insight, InfoGibraltar, Olive Press)',news('("La Linea" OR "La Línea" OR Spain OR frontier OR border) (site:chronicle.gi OR site:gbc.gi OR site:yourgibraltartv.com OR site:panorama.gi OR site:gibraltarinsight.com OR site:infogibraltar.com OR site:theolivepress.es) when:2d'),'rss',72,0,0),
      ('Gibraltar · Policía y Puerto',news('("La Linea" OR Spain OR Spanish) (site:police.gi OR site:gibraltarport.com) when:7d'),'rss',62,1,0),
      ('Cultura de La Línea (Museo Cruz Herrera, Manolo Alés, Teatro, Palacio de Congresos, Biblioteca)',news('("Museo Cruz Herrera" OR "Manolo Alés" OR "Teatro Paseo de la Velada" OR "Palacio de Congresos" OR "Biblioteca José Riquelme" OR "Biblioteca Municipal José Riquelme") "La Línea" when:7d'),'rss',84,1,1),
      ('Redes municipales (Turismo, Museo, Galería, Teatro, Palacio, Biblioteca)',news('("Turismo La Línea" OR "Museo Cruz Herrera" OR "Manolo Alés" OR "Teatro Paseo de la Velada" OR "Palacio de Congresos de La Línea" OR "José Riquelme") (site:facebook.com OR site:instagram.com) when:7d'),'rss',70,1,1),
      ('Áreas municipales (Urbanismo, Infraestructuras, EMUSVIL, Medio Ambiente, Movilidad, Deportes, Festejos, Seguridad)',news('"La Línea" (Urbanismo OR Infraestructuras OR EMUSVIL OR "Medio Ambiente" OR Movilidad OR Deportes OR Festejos OR "Seguridad Ciudadana") Ayuntamiento when:3d'),'rss',82,1,0),
      ('Transparencia y publicaciones oficiales del Ayuntamiento',news('"La Línea de la Concepción" (transparencia OR "Junta de Gobierno" OR pleno OR decreto OR ordenanza) when:7d'),'rss',80,1,0),
      ('BOE · La Línea',news('"La Línea de la Concepción" site:boe.es when:7d'),'rss',80,1,0),
    ]
    for n,u,k,p,o,scope in more:
        c.execute('INSERT OR IGNORE INTO sources(name,url,kind,priority,official,local_scope) VALUES(?,?,?,?,?,?)',(n,u,k,p,o,scope))
    # Fuentes oficiales prioritarias del Ayuntamiento: feeds de licitaciones y
    # contratos menores de PLACSP, además del tablón electrónico municipal.
    c.execute("UPDATE sources SET name='Licitaciones del Ayuntamiento · PLACSP',official=1,local_scope=1,priority=100 WHERE url=?",
              ('https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom',))
    c.execute("UPDATE sources SET name='Contratos menores · PLACSP',official=1,local_scope=1,priority=98 WHERE url=?",
              ('https://contrataciondelestado.es/sindicacion/sindicacion_1143/contratosMenoresPerfilesContratantes.atom',))
    # La consulta indexada está limitada a la sede municipal y puede recoger
    # edictos cuyo título no repite el nombre de la ciudad.
    c.execute("UPDATE sources SET local_scope=1 WHERE name='Edictos · sede municipal indexada'")
    # El tablón de edictos es fuente prioritaria: se reactiva una vez (5.6)
    # Solo La Línea (5.7): «Campo de Gibraltar» trae otros municipios; Gibraltar pasa por el filtro de temas
    if not c.execute("SELECT 1 FROM settings WHERE key='solo_linea_57'").fetchone():
        c.execute("UPDATE sources SET active=0 WHERE name='Nacionales adaptables · Campo de Gibraltar'")
        c.execute("UPDATE sources SET local_scope=0 WHERE name LIKE 'Nacionales adaptables%'")
        c.execute("INSERT INTO settings(key,value) VALUES('solo_linea_57',datetime('now'))")
    if not c.execute("SELECT 1 FROM settings WHERE key='edictos_on_56'").fetchone():
        c.execute("UPDATE sources SET active=1,priority=99,last_error=NULL WHERE kind='edictos' OR url LIKE 'https://www.sedeelectronica.lalinea.es/edictos/%'")
        c.execute("UPDATE sources SET active=1 WHERE name='Edictos · sede municipal indexada'")
        c.execute("INSERT INTO settings(key,value) VALUES('edictos_on_56',datetime('now'))")
    # Las búsquedas indexadas de Facebook/Instagram pasan al panel Redes (allí se filtran ventas y publicidad).
    c.execute("UPDATE sources SET active=0 WHERE name IN ('Facebook · publicaciones indexadas','Instagram · publicaciones indexadas','Instagram · Turismo local')")
    # Empezar de cero (una sola vez, versión 3.5): se archiva todo lo pendiente; lo publicado se conserva.
    if not c.execute("SELECT 1 FROM settings WHERE key='fresh_start_35'").fetchone():
        c.execute("UPDATE articles SET status='rejected' WHERE status IN ('draft','review_ready','approved')")
        c.execute("UPDATE candidates SET status='archived',editorial_priority='undecided',planned_at=NULL,plan_locked=0,work_state=NULL "
                  "WHERE status!='published'")
        c.execute("INSERT INTO settings(key,value) VALUES('fresh_start_35',datetime('now'))")
    # Empezar de cero de nuevo (4.2): se borran las noticias encontradas (no las publicadas) para releer todas las fuentes.
    if not c.execute("SELECT 1 FROM settings WHERE key='fresh_start_42'").fetchone():
        c.execute("DELETE FROM articles WHERE status!='published'")
        c.execute("DELETE FROM candidates WHERE id NOT IN (SELECT candidate_id FROM articles WHERE candidate_id IS NOT NULL)")
        c.execute("UPDATE sources SET last_checked_at=NULL,last_error=NULL,items_seen=0,items_added=0")
        c.execute("DELETE FROM canva_designs WHERE article_id NOT IN (SELECT id FROM articles)")
        c.execute("INSERT INTO settings(key,value) VALUES('fresh_start_42',datetime('now'))")
    c.commit(); c.close()

def rows(sql,args=()):
    c=conn(); r=[dict(x) for x in c.execute(sql,args).fetchall()]; c.close(); return r

def row(sql,args=()):
    c=conn(); r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None

def exec_(sql,args=()):
    c=conn(); cur=c.execute(sql,args); c.commit(); rid=cur.lastrowid; c.close(); return rid

def get_setting(key,default=''):
    r=row('SELECT value FROM settings WHERE key=?',(key,))
    return r['value'] if r else default
def set_setting(key,value):
    exec_('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,str(value)))
def log(kind,msg):
    exec_('INSERT INTO activity(kind,message) VALUES(?,?)',(kind,msg))
