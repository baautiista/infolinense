"""Ajustes de Área Campo de Gibraltar Desk (variables de Railway)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _on(name, default='false'):
    return os.getenv(name, default).lower() in {'1', 'true', 'yes', 'on'}


DATA_DIR = Path(os.getenv('DATA_DIR', str(BASE_DIR / 'data')))
DB_PATH = Path(os.getenv('DB_PATH', str(DATA_DIR / 'area.db')))
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', str(DATA_DIR / 'uploads')))
RENDER_DIR = Path(os.getenv('RENDER_DIR', str(DATA_DIR / 'renders')))
DOCS_DIR = Path(os.getenv('DOCS_DIR', str(DATA_DIR / 'docs')))

# IA (igual que InfoLinense Desk). Por defecto, modo sin coste: solo Gemini gratis.
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.1').strip()
OPENAI_WEB_SEARCH = _on('OPENAI_WEB_SEARCH', 'true')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '').strip()
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5-5').strip()
ANTHROPIC_WORKSPACE_ID = os.getenv('ANTHROPIC_WORKSPACE_ID', '').strip()
ANTHROPIC_WEB_SEARCH = _on('ANTHROPIC_WEB_SEARCH')
AI_ALLOW_PAID = _on('AI_ALLOW_PAID')
def _key(name):
    """Clave tal cual, aunque se haya pegado con comillas, espacios o con «NOMBRE=» delante."""
    v = os.getenv(name, '').strip().strip('"').strip("'").strip()
    if v.upper().startswith(name + '='):
        v = v[len(name) + 1:].strip().strip('"').strip("'")
    return ''.join(v.split())


GEMINI_API_KEY = _key('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest').strip()
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv('GEMINI_FALLBACK_MODELS', 'gemini-2.5-flash-lite,gemini-flash-latest,gemini-2.5-flash').split(',') if m.strip()]
GEMINI_MIN_INTERVAL = float(os.getenv('GEMINI_MIN_INTERVAL', '13'))
AI_PROVIDER = os.getenv('AI_PROVIDER', 'auto').strip().lower()
AI_FALLBACK = _on('AI_FALLBACK', 'true')
GOOGLE_SEARCH_API_KEY = os.getenv('GOOGLE_SEARCH_API_KEY', '').strip()
GOOGLE_SEARCH_CX = os.getenv('GOOGLE_SEARCH_CX', '').strip()

# Radar
SCAN_INTERVAL_MINUTES = int(os.getenv('SCAN_INTERVAL_MINUTES', '45'))
MAX_CANDIDATE_AGE_DAYS = int(os.getenv('MAX_CANDIDATE_AGE_DAYS', '3'))
TENDER_WINDOW_DAYS = int(os.getenv('TENDER_WINDOW_DAYS', '20'))
# Páginas del ATOM de la Plataforma de Contratación que se leen en cada búsqueda (cada una trae ~500 expedientes de toda España)
PLACSP_FIRST_PAGES = int(os.getenv('PLACSP_FIRST_PAGES', '12'))
PLACSP_MAX_PAGES = int(os.getenv('PLACSP_MAX_PAGES', '40'))
# Redactar sola la propuesta del día y lo que se marca como Urgente/Hoy
AUTO_DRAFT = _on('AUTO_DRAFT', 'true')
# Hora (Madrid) a la que se elige y redacta la propuesta del día
DAILY_PICK_HOUR = int(os.getenv('DAILY_PICK_HOUR', '7'))

# Diario Área: lo ya publicado allí no se vuelve a proponer
AREA_SITE = os.getenv('AREA_SITE', 'https://www.diarioarea.com').rstrip('/')
AREA_FEED_PAGES = int(os.getenv('AREA_FEED_PAGES', '5'))

# Acceso
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '').strip()
JWT_SECRET = os.getenv('JWT_SECRET', '').strip()
TOKEN_TTL_HOURS = int(os.getenv('TOKEN_TTL_HOURS', '168'))
CORS_ORIGINS = [x.strip() for x in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:8000').split(',') if x.strip()]
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')

# Canva (plantilla de marca del carrusel; se puede cambiar en Ajustes)
CANVA_CLIENT_ID = os.getenv('CANVA_CLIENT_ID', '').strip()
CANVA_CLIENT_SECRET = os.getenv('CANVA_CLIENT_SECRET', '').strip()
CANVA_TEMPLATE_ID = os.getenv('CANVA_TEMPLATE_ID', '').strip()

for d in (DATA_DIR, UPLOAD_DIR, RENDER_DIR, DOCS_DIR):
    d.mkdir(parents=True, exist_ok=True)
