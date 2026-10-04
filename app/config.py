import os, secrets
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DATA_DIR = Path(os.getenv('DATA_DIR', str(BASE_DIR / 'data')))
RENDER_DIR = Path(os.getenv('RENDER_DIR', str(BASE_DIR / 'renders')))
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', str(BASE_DIR / 'uploads')))
TEMPLATE_DIR = Path(os.getenv('TEMPLATE_DIR', str(BASE_DIR / 'templates')))
DB_PATH = Path(os.getenv('DB_PATH', str(DATA_DIR / 'infolinense.db')))
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY','').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL','gpt-5.1').strip()
OPENAI_WEB_SEARCH = os.getenv('OPENAI_WEB_SEARCH','true').lower() in {'1','true','yes','on'}
# Claude API credentials remain server-side in Railway; never expose them to the browser.
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY','').strip()
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL','claude-sonnet-5-5').strip()
ANTHROPIC_WORKSPACE_ID = os.getenv('ANTHROPIC_WORKSPACE_ID','').strip()
ANTHROPIC_WEB_SEARCH = os.getenv('ANTHROPIC_WEB_SEARCH','false').lower() in {'1','true','yes','on'}
AI_PROVIDER = os.getenv('AI_PROVIDER','auto').strip().lower()
# Si el proveedor principal falla (saldo, límite, caída), probar el otro si tiene clave.
AI_FALLBACK = os.getenv('AI_FALLBACK','true').lower() in {'1','true','yes','on'}
SCAN_INTERVAL_MINUTES = int(os.getenv('SCAN_INTERVAL_MINUTES','60'))
MAX_CANDIDATE_AGE_DAYS = int(os.getenv('MAX_CANDIDATE_AGE_DAYS','3'))
AUTO_DRAFTS_PER_SCAN = int(os.getenv('AUTO_DRAFTS_PER_SCAN','4'))
# Redactar automáticamente las noticias marcadas como Urgente/Hoy/Esta semana/Futura
AUTO_DRAFT_USEFUL = os.getenv('AUTO_DRAFT_USEFUL','true').lower() in {'1','true','yes','on'}
AUTO_PIPELINE = os.getenv('AUTO_PIPELINE','false').lower() in {'1','true','yes','on'}
QUICK_SCORE_MAX = int(os.getenv('QUICK_SCORE_MAX','69'))
MIN_AUTO_SCORE = int(os.getenv('MIN_AUTO_SCORE','42'))
PUBLISH_MODE = os.getenv('PUBLISH_MODE','none').strip().lower()
WORDPRESS_URL = os.getenv('WORDPRESS_URL','').strip().rstrip('/')
WORDPRESS_USERNAME = os.getenv('WORDPRESS_USERNAME','').strip()
WORDPRESS_APP_PASSWORD = os.getenv('WORDPRESS_APP_PASSWORD','').strip()
LOVABLE_WEBHOOK_URL = os.getenv('LOVABLE_WEBHOOK_URL','').strip()
LOVABLE_WEBHOOK_TOKEN = os.getenv('LOVABLE_WEBHOOK_TOKEN','').strip()
SUPABASE_URL = os.getenv('SUPABASE_URL','').rstrip('/')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY','').strip()
SUPABASE_ARTICLES_TABLE = os.getenv('SUPABASE_ARTICLES_TABLE','articles').strip()
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD','').strip()
JWT_SECRET = os.getenv('JWT_SECRET','').strip()
TOKEN_TTL_HOURS = int(os.getenv('TOKEN_TTL_HOURS','168'))
CORS_ORIGINS = [x.strip() for x in os.getenv('CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:8000').split(',') if x.strip()]
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL','').rstrip('/')
for d in (DATA_DIR, RENDER_DIR, UPLOAD_DIR, TEMPLATE_DIR): d.mkdir(parents=True, exist_ok=True)
