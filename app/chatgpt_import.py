import hashlib
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints

from . import brands, canva, db, layout
from .auth import require_auth
from .config import BASE_DIR

router = APIRouter()
canva_lock = threading.Lock()


class ChatGPTDraft(BaseModel):
    article_id: int | None = Field(default=None, gt=0)
    headline: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
    subtitle: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
    brand: str | None = Field(default=None, max_length=40)
    section: str = Field(default='', max_length=100)


@router.get('/enviar-a-desk', include_in_schema=False)
def send_to_desk_page():
    return FileResponse(
        BASE_DIR / 'static' / 'send-to-desk.html',
        headers={
            'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Referrer-Policy': 'no-referrer',
        },
    )


@router.post('/api/import/chatgpt/draft', dependencies=[Depends(require_auth)])
def save_draft(draft: ChatGPTDraft):
    with closing(db.conn()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        if draft.article_id:
            existing = connection.execute('SELECT * FROM articles WHERE id=?', (draft.article_id,)).fetchone()
            if not existing:
                raise HTTPException(404, 'El ID indicado no existe en Desk.')
        else:
            digest = hashlib.sha256(draft.model_dump_json().encode()).hexdigest()
            candidate_url = 'manual:chatgpt-' + digest
            existing = connection.execute(
                'SELECT a.* FROM articles a JOIN candidates c ON c.id=a.candidate_id WHERE c.url=?',
                (candidate_url,),
            ).fetchone()

        if existing and (
            existing['status'] not in ('draft', 'review_ready', 'approved')
            or existing['scheduled_at'] or existing['hidden']
        ):
            raise HTTPException(409, 'La noticia está publicada, programada o archivada en Desk.')

        brand = draft.brand or (existing['brand'] if existing else None) or brands.DEFAULT
        if brand not in brands.BRANDS:
            raise HTTPException(422, 'El medio indicado no existe en Desk.')
        content = ' '.join((draft.headline, draft.subtitle, draft.body[:1500]))
        same_brand = existing and existing['brand'] == brand
        section = draft.section or (existing['section'] if same_brand else '') or ''
        if brand == brands.DEFAULT:
            section = layout.normalize_section(section, content)
        else:
            section = brands.find_section(brand, section) or brands.detect_section(brand, content) or ''
        fields = (draft.headline, draft.subtitle, draft.body, brand, section)

        if existing:
            article_id = existing['id']
            if tuple(existing[key] for key in ('headline', 'subtitle', 'body', 'brand', 'section')) != fields:
                same_design = all(
                    existing[key] == value for key, value in
                    (('headline', draft.headline), ('subtitle', draft.subtitle), ('brand', brand), ('section', section))
                )
                connection.execute(
                    """UPDATE articles SET headline=?,subtitle=?,body=?,brand=?,section=?,social_text=?,
                       status='review_ready',render_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (*fields, draft.body, existing['render_path'] if same_design else None, article_id),
                )
                connection.execute(
                    "UPDATE candidates SET brand=?,section=?,status='review_ready' WHERE id=?",
                    (brand, section, existing['candidate_id']),
                )
        else:
            candidate = connection.execute(
                """INSERT INTO candidates
                   (source_name,outlet,title,url,published_at,excerpt,raw_text,score,relevance,status,editorial_priority,brand,section)
                   VALUES('Redacción','Redacción',?,?,?,?,?,90,'high','review_ready','today',?,?)""",
                (draft.headline, candidate_url, datetime.now(timezone.utc).isoformat(),
                 draft.subtitle, draft.body, brand, section),
            )
            article_id = connection.execute(
                """INSERT INTO articles (headline,subtitle,body,brand,section,candidate_id,social_text,status)
                   VALUES(?,?,?,?,?,?,?,'review_ready')""",
                (*fields, candidate.lastrowid, draft.body),
            ).lastrowid
    return {'id': article_id}


@router.post('/api/import/chatgpt/draft/{article_id}/canva', dependencies=[Depends(require_auth)])
def prepare_canva(article_id: int):
    if not canva_lock.acquire(blocking=False):
        raise HTTPException(409, 'Desk está preparando otro diseño. Reintenta en unos segundos.')
    try:
        article = db.row(
            """SELECT * FROM articles WHERE id=? AND status IN ('draft','review_ready','approved')
               AND scheduled_at IS NULL AND COALESCE(hidden,0)=0""",
            (article_id,),
        )
        if not article:
            raise HTTPException(404, 'La noticia ya no está disponible como borrador.')
        photo = article.get('image_local')
        has_photo = photo and article.get('image_kind') != 'final_image' and Path(photo).is_file()
        content_hash = canva._content_hash(article)
        if not has_photo:
            content_hash = 'text:' + content_hash
        previous = db.row(
            'SELECT url FROM canva_designs WHERE article_id=? AND content_hash=?',
            (article_id, content_hash),
        )
        if previous and previous['url']:
            return {'url': previous['url'], 'needs_photo': not has_photo}
        try:
            result = canva.create_design(article) if has_photo else canva.create_text_design(article, content_hash)
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {'url': result['url'], 'needs_photo': not has_photo}
    finally:
        canva_lock.release()
