import os, json, shutil, tempfile, subprocess, zipfile
import textwrap
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageOps
from pptx import Presentation
from pptx.util import Pt
from .config import BASE_DIR, RENDER_DIR
from . import db

SECTION_MAP={'URBANISMO':1,'CIUDAD':2,'GIBRALTAR':3,'SUCESOS':4,'CULTURA':5,'DEPORTES':6,'COMERCIO':7,'MEDIO AMBIENTE':8,'POLÍTICA':9,'SOCIEDAD':10,'PATRIMONIO':11,'AGENDA':12}

def _replace_text_keep_style(shape,text,fit_title=False):
    tf=shape.text_frame
    # preserve first run formatting as far as possible
    proto=None
    for p in tf.paragraphs:
        if p.runs:
            r=p.runs[0]; proto=(r.font.name,r.font.size,r.font.bold,r.font.italic,r.font.color.type, getattr(r.font.color,'rgb',None))
            break
    tf.clear(); content_lines=text.split('\n')
    for i,line in enumerate(content_lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph(); r=p.add_run(); r.text=line
        if proto:
            name,size,bold,italic,ctype,rgb=proto
            if name:r.font.name=name
            if size:
                if fit_title:
                    # Canva's title box has a fixed size. Fit both its height and
                    # its longest line instead of letting PowerPoint overflow.
                    longest=max((len(s) for s in content_lines),default=1)
                    font_pt=min(size.pt, shape.height.pt / (max(1,len(content_lines))*1.19), shape.width.pt / (max(1,longest)*0.61))
                    r.font.size=Pt(max(18,font_pt))
                else:
                    longest=max((len(s) for s in content_lines),default=1)
                    font_pt=min(size.pt,shape.height.pt/(max(1,len(content_lines))*1.18),shape.width.pt/(max(1,longest)*0.56))
                    r.font.size=Pt(max(12,font_pt))
            if bold is not None:r.font.bold=bold
            if italic is not None:r.font.italic=italic
            if rgb is not None:
                try:r.font.color.rgb=rgb
                except:pass

def _fit_title(title):
    # The exported Canva artwork has room for three lines. Keep full headlines
    # where possible and shorten only those too long to remain legible.
    title=' '.join((title or '').split())
    if len(title)>116: title=title[:115].rsplit(' ',1)[0]+'…'
    width=max(19,(len(title)+2)//3+2)
    lines=textwrap.wrap(title,width=width,break_long_words=False,break_on_hyphens=False)
    while len(lines)>3 and width<58:
        width+=2
        lines=textwrap.wrap(title,width=width,break_long_words=False,break_on_hyphens=False)
    return '\n'.join(lines[:3])

def _fit_summary(summary):
    summary=' '.join((summary or '').upper().split())
    if len(summary)>132: summary=summary[:131].rsplit(' ',1)[0]+'…'
    width=max(35,(len(summary)+2)//3+3)
    lines=textwrap.wrap(summary,width=width,break_long_words=False,break_on_hyphens=False)
    while len(lines)>3 and width<78:
        width+=2
        lines=textwrap.wrap(summary,width=width,break_long_words=False,break_on_hyphens=False)
    return '\n'.join(lines[:3])

def _crop_to(im,size):
    return ImageOps.fit(im,size,method=Image.Resampling.LANCZOS,centering=(0.5,0.5))

def render_article(article_id):
    art=db.row('SELECT * FROM articles WHERE id=?',(article_id,));
    if not art: raise RuntimeError('Artículo no existe')
    tpl=db.row('SELECT * FROM templates WHERE id=?',(art.get('template_id') or 1,)) or db.row('SELECT * FROM templates WHERE active=1 LIMIT 1')
    template_path=Path(tpl['path'])
    if not template_path.is_absolute(): template_path=BASE_DIR/template_path
    section=(art.get('section') or 'CIUDAD').upper(); slide_no=json.loads(tpl.get('slide_map_json') or '{}').get(section,SECTION_MAP.get(section,2))
    with tempfile.TemporaryDirectory() as td:
        work=Path(td)/'work.pptx'; shutil.copy(template_path,work)
        prs=Presentation(work)
        if slide_no<1 or slide_no>len(prs.slides): raise RuntimeError('La plantilla no tiene la diapositiva de esta sección')
        slide=prs.slides[slide_no-1]
        # Replace the PHOTO shape, or the largest picture fill in the chosen slide.
        if art.get('image_local') and Path(art['image_local']).exists():
            pictures=[]
            for sh in slide.shapes:
                for blip in sh._element.xpath('.//a:blip'):
                    rid=blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    if rid:
                        part=slide.part.related_part(rid)
                        pictures.append((1 if sh.name.upper()=='PHOTO' else 0,sh.width*sh.height,str(part.partname).lstrip('/')))
            if not pictures: raise RuntimeError('La plantilla no contiene un espacio de fotografía')
            media_path=max(pictures)[2]
            outzip=Path(td)/'repacked.pptx'
            with zipfile.ZipFile(work,'r') as zin, zipfile.ZipFile(outzip,'w',zipfile.ZIP_DEFLATED) as zout:
                orig=Image.open(BytesIO(zin.read(media_path))); target=orig.size
                newim=_crop_to(Image.open(art['image_local']).convert('RGB'),target)
                bio=BytesIO(); newim.save(bio,'PNG' if orig.format=='PNG' else 'JPEG',quality=94); newbytes=bio.getvalue()
                for item in zin.infolist():
                    data=newbytes if item.filename==media_path else zin.read(item.filename)
                    zout.writestr(item,data)
            shutil.move(outzip,work)
        prs=Presentation(work)
        slide=prs.slides[slide_no-1]
        # known text shapes in provided template
        title=_fit_title(art.get('headline') or '')
        summary=_fit_summary(art.get('graphic_summary') or art.get('subtitle') or '')
        for sh in slide.shapes:
            if not getattr(sh,'has_text_frame',False): continue
            t=(sh.text or '').strip()
            if '{{HEADLINE}}' in t or t.startswith('Así será la nueva') or ('Plaza de la' in t and 'Constitución' in t): _replace_text_keep_style(sh,t.replace('{{HEADLINE}}',title) if '{{HEADLINE}}' in t else title,fit_title=True)
            elif '{{SUMMARY}}' in t or t.startswith('EL PROYECTO DE 5 MILLONES'): _replace_text_keep_style(sh,t.replace('{{SUMMARY}}',summary) if '{{SUMMARY}}' in t else summary)
            elif '{{SECTION}}' in t: _replace_text_keep_style(sh,t.replace('{{SECTION}}',section))
        edited=Path(td)/'edited.pptx'; prs.save(edited)
        outdir=Path(td)/'pdf'; outdir.mkdir()
        subprocess.run(['libreoffice','--headless','--convert-to','pdf','--outdir',str(outdir),str(edited)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        pdf=outdir/'edited.pdf'
        raw=Path(td)/'page'
        subprocess.run(['pdftoppm','-f',str(slide_no),'-singlefile','-png','-r','120',str(pdf),str(raw)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        im=Image.open(str(raw)+'.png').convert('RGB'); im=im.resize((1080,1350),Image.Resampling.LANCZOS)
        final=RENDER_DIR/f'article_{article_id}.png'; im.save(final,'PNG',optimize=True)
    db.exec_('UPDATE articles SET render_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',(str(final),article_id))
    return str(final)
