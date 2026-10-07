"""Pruebas sin red: municipios, Plataforma de Contratación, cobertura de Área/competencia, borrador e imágenes de PDF."""
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ['DATA_DIR'] = TMP
os.environ['GEMINI_API_KEY'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, sources, docs, redaccion, efemerides  # noqa: E402

db.init_db()
NOW = datetime.now(timezone.utc)


def test_towns():
    assert sources.towns_in('Algeciras licita la reforma del parque María Cristina') == ['Algeciras']
    assert sources.towns_in('El Ayuntamiento de San Roque aprueba obras en Puente Mayorga') == ['San Roque']
    assert 'Tarifa' not in sources.towns_in('Sube la tarifa de la luz este mes')
    assert 'Tarifa' not in sources.towns_in('La Tarifa de último recurso del gas baja')
    assert sources.towns_in('Obras en la playa de Tarifa este verano') == ['Tarifa']
    assert 'La Línea' not in sources.towns_in('La línea 3 del metro de Madrid cierra')
    assert sources.comarca_ok('El puerto de Algeciras bate su récord de contenedores')
    assert sources.comarca_ok('Nueva licitación de la Mancomunidad de Municipios del Campo de Gibraltar')
    assert not sources.comarca_ok('Fiesta de San Roque en Calahorra')
    assert not sources.comarca_ok('Castellar del Vallès estrena biblioteca')


ATOM = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cbc="urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2"
 xmlns:cac="urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2"
 xmlns:cac-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2">
 <link rel="next" href="https://example.org/next.atom"/>
 <entry>
  <id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/111</id>
  <link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=abc"/>
  <title>Reurbanizacion de la calle Ancha</title>
  <updated>__NOW__</updated>
  <cac-place-ext:ContractFolderStatus>
   <cbc:ContractFolderID>OBR-12/2026</cbc:ContractFolderID>
   <cbc-place-ext:ContractFolderStatusCode>PUB</cbc-place-ext:ContractFolderStatusCode>
   <cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName><cbc:Name>Junta de Gobierno Local del Ayuntamiento de Algeciras</cbc:Name></cac:PartyName>
    <cac:PostalAddress><cbc:CityName>Algeciras</cbc:CityName></cac:PostalAddress></cac:Party></cac-place-ext:LocatedContractingParty>
   <cac:ProcurementProject><cbc:Name>Reurbanización de la calle Ancha y su entorno</cbc:Name><cbc:TypeCode>3</cbc:TypeCode>
    <cac:BudgetAmount><cbc:TotalAmount>1452000.50</cbc:TotalAmount><cbc:TaxExclusiveAmount>1200000.00</cbc:TaxExclusiveAmount></cac:BudgetAmount>
    <cac:RealizedLocation><cbc:CountrySubentity>Cádiz</cbc:CountrySubentity><cac:Address><cbc:CityName>Algeciras</cbc:CityName></cac:Address></cac:RealizedLocation>
   </cac:ProcurementProject>
   <cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>2026-11-02</cbc:EndDate></cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess>
   <cac:LegalDocumentReference><cbc:ID>PCAP.pdf</cbc:ID><cac:Attachment><cac:ExternalReference><cbc:URI>https://example.org/pcap.pdf</cbc:URI></cac:ExternalReference></cac:Attachment></cac:LegalDocumentReference>
   <cac:TechnicalDocumentReference><cbc:ID>Proyecto de ejecucion.pdf</cbc:ID><cac:Attachment><cac:ExternalReference><cbc:URI>https://example.org/proyecto.pdf</cbc:URI></cac:ExternalReference></cac:Attachment></cac:TechnicalDocumentReference>
  </cac-place-ext:ContractFolderStatus>
 </entry>
 <entry>
  <title>Suministro de papel</title><updated>__NOW__</updated>
  <link href="https://contrataciondelestado.es/x2"/>
  <cac-place-ext:ContractFolderStatus><cbc-place-ext:ContractFolderStatusCode>PUB</cbc-place-ext:ContractFolderStatusCode>
   <cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName><cbc:Name>Ayuntamiento de Valladolid</cbc:Name></cac:PartyName></cac:Party></cac-place-ext:LocatedContractingParty>
   <cac:ProcurementProject><cbc:Name>Suministro de papel</cbc:Name></cac:ProcurementProject>
  </cac-place-ext:ContractFolderStatus>
 </entry>
</feed>'''.replace('__NOW__', NOW.isoformat()).encode('utf-8')


class _Resp:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        pass


def test_placsp(monkeypatch=None):
    orig = sources.fetch
    sources.fetch = lambda url, timeout=25, **kw: _Resp(ATOM if 'next' not in url else b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    try:
        items = sources.parse_placsp({'url': 'https://example.org/feed.atom'})
    finally:
        sources.fetch = orig
    assert len(items) == 1, items
    it = items[0]
    assert it['title'].startswith('Licitación: Reurbanización de la calle Ancha')
    assert '1.200.000,00 €' in it['excerpt'] and 'Obras' in it['excerpt'] and 'Plazo hasta 2026-11-02' in it['excerpt']
    assert [d['name'] for d in it['docs']] == ['PCAP.pdf', 'Proyecto de ejecucion.pdf']
    src = db.row("SELECT * FROM sources WHERE kind='placsp' LIMIT 1")
    cid = sources.add_candidate(it, src)
    c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
    assert c['block'] == 'Licitaciones' and c['tag'] == 'licitacion' and c['towns'] == 'Algeciras' and c['tstatus'] == 'PUB'
    assert c['amount_value'] == 1200000.0
    assert c['exclusive'] == 1 and c['score'] >= 70, c['score']
    # documentos: el proyecto va antes que el pliego administrativo
    assert docs.doc_links({**c, 'url': 'efemeride://x'})[0]['name'] == 'Proyecto de ejecucion.pdf'


def _press(role, outlet, title, hours_ago):
    db.exec_('INSERT OR IGNORE INTO press(role,outlet,title,url,published_at) VALUES(?,?,?,?,?)',
             (role, outlet, title, 'https://x.org/' + str(abs(hash(title))), (NOW - timedelta(hours=hours_ago)).isoformat()))


def test_coverage_area_and_competitors():
    src = {'id': None, 'name': 'Ayuntamiento de Los Barrios', 'block': 'Instituciones', 'priority': 90, 'official': 1, 'kind': 'rss'}
    _press('area', 'Diario Área', 'Los Barrios abre el plazo de inscripción para la ruta en kayak por el río Palmones', 5)
    cid = sources.add_candidate({'title': 'Turismo de Los Barrios organiza una ruta en kayak por el río Palmones: inscripción abierta',
                                 'url': 'https://www.losbarrios.es/kayak', 'published_at': NOW.isoformat()}, src)
    c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
    assert c['area_state'] == 'published' and c['status'] == 'in_area', c
    # Actualización: Área lo publicó hace días y ahora hay una fase nueva con cifra nueva
    _press('area', 'Diario Área', 'San Roque proyecta un nuevo centro de salud en Taraguilla', 72)
    cid2 = sources.add_candidate({'title': 'San Roque adjudica por 2.300.000 euros el nuevo centro de salud de Taraguilla',
                                  'url': 'https://www.sanroque.es/cs', 'published_at': NOW.isoformat()}, src)
    c2 = db.row('SELECT * FROM candidates WHERE id=?', (cid2,))
    assert c2['area_state'] == 'update' and c2['status'] == 'new', c2
    # Competencia: deja de ser exclusiva
    _press('competitor', 'Europa Sur', 'Tarifa programa conciertos gratis en la plaza de Santa María este fin de semana', 3)
    cid3 = sources.add_candidate({'title': 'Conciertos gratis en la plaza de Santa María de Tarifa este fin de semana',
                                  'url': 'https://www.aytotarifa.com/conciertos', 'published_at': NOW.isoformat()}, src)
    c3 = db.row('SELECT * FROM candidates WHERE id=?', (cid3,))
    assert c3['exclusive'] == 0 and json.loads(c3['competitors_json'])[0]['outlet'] == 'Europa Sur'
    assert c3['block'] == 'Agenda y cultura'


def test_free_draft_and_slides():
    c = db.row("SELECT * FROM candidates WHERE tag='licitacion' LIMIT 1")
    d = redaccion.free_draft(c, 'El proyecto prevé ampliar las aceras y plantar 40 árboles en la calle Ancha de Algeciras durante 10 meses de obra.')
    assert d['headline'].startswith('Reurbanización') and [x['layout'] for x in d['slides']][:2] == ['portada', 'ficha']
    assert d['slides'][1]['figure'] == '1.200.000 €' and d['slides'][1]['status'] == 'En licitación'
    assert '#CampoDeGibraltar' in d['instagram_copy'] and '#Algeciras' in d['instagram_copy']
    assert d['render']['recommended'] is True


def test_pdf_images():
    from PIL import Image
    im = Image.new('RGB', (900, 600), (200, 30, 40))
    buf = io.BytesIO()
    im.save(buf, 'PDF')
    folder = Path(TMP) / 'pdf'
    folder.mkdir(exist_ok=True)
    out = docs.images_from_pdf(buf.getvalue(), folder, 'Proyecto')
    assert out and out[0]['width'] >= 900 and Path(out[0]['path']).is_file()


def test_efemerides():
    from datetime import date
    up = efemerides.upcoming(5, start=date(2026, 7, 18))
    lin = [e for e in up if 'La Línea' in e['title']]
    assert lin and lin[0]['years'] == 156 and lin[0]['in_days'] == 2
    up = efemerides.upcoming(3, start=date(2029, 6, 7))
    verja = [e for e in up if 'Verja' in e['title']][0]
    assert verja['years'] == 60 and verja['round']



def test_ai_draft_parsing():
    from app import ai
    fake = {'focus': 'Licitación', 'section': 'licitaciones', 'town': 'Algeciras', 'headline': 'Algeciras licita por 1,2 millones la calle Ancha',
            'headline_options': ['A', 'B'], 'entradilla': 'Diez meses de obra. No consta la empresa.', 'body': 'Párrafo uno.\n\nSe desconoce el inicio. Habrá 40 árboles.',
            'instagram_copy': '🏗 Así será la calle Ancha\n\n#CampoDeGibraltar', 'slides': [{'role': 'portada', 'title': 'Así será la calle Ancha'},
            {'layout': 'ficha', 'title': 'Calle Ancha', 'text': 'Presupuesto de licitación', 'status': 'En licitación', 'figure': '1,2 M€'},
            {'layout': 'caja', 'title': '¿Qué incluye?', 'bullets': ['Aceras', '40 árboles']}, {'layout': 'inventado', 'title': 'X'},
            {'layout': 'portada', 'title': 'ASÍ SERÁ LA CALLE ANCHA'}],
            'render': {'recommended': True, 'why': 'Cambia la calle', 'prompt': 'Vista peatonal…', 'basis': 'Proyecto'}, 'photo_query': 'calle Ancha Algeciras', 'missing': ['Empresa']}
    old_enabled, old_ask = ai.AI_ENABLED, redaccion.ask_json
    ai.AI_ENABLED = True
    redaccion.ask_json = lambda *a, **k: (fake, 'gemini', False)
    try:
        d = redaccion.draft({'title': 'Licitación: calle Ancha', 'block': 'Exclusivas', 'tag': 'licitacion', 'towns': 'Algeciras'})
    finally:
        ai.AI_ENABLED, redaccion.ask_json = old_enabled, old_ask
    assert d['section'] == 'LICITACIONES' and d['entradilla'] == 'Diez meses de obra.' and 'desconoce' not in d['body']
    assert [x['layout'] for x in d['slides']] == ['portada', 'ficha', 'caja', 'lista'], d['slides']
    assert d['slides'][0]['title'] == 'Así será la calle Ancha' and d['slides'][2]['bullets'] == ['Aceras', '40 árboles'] and d['render']['recommended']


def test_canva_fields():
    from app import canva
    assert canva.field_role('TITULO_3') == ('title', 3)
    assert canva.field_role('Foto 1') == ('photo', 1)
    assert canva.field_role('S2_TEXTO') == ('text', 2)
    assert canva.field_role('MUNICIPIO') == ('town', None)
    assert canva.field_role('ANTETÍTULO_2') == ('kicker', 2)
    fields = [{'name': 'TITULO_1', 'type': 'text', 'role': 'title', 'slide': 1}, {'name': 'TEXTO_2', 'type': 'text', 'role': 'text', 'slide': 2},
              {'name': 'TITULO_9', 'type': 'text', 'role': 'title', 'slide': 9}, {'name': 'NUM_2', 'type': 'text', 'role': 'num', 'slide': 2}]
    data = canva.build_data(fields, [{'title': 'Portada'}, {'title': 'Dos', 'text': 'Texto dos'}], [], 'Algeciras', 1)
    assert data['TITULO_1']['text'] == 'Portada' and data['TEXTO_2']['text'] == 'Texto dos' and data['TITULO_9']['text'] == ' '
    assert data['NUM_2']['text'] == '2/2'


def test_tender_towns_and_merge():
    assert sources.town_from_postal('11202') == 'Algeciras' and sources.town_from_postal('11300') == 'La Línea'
    assert sources.town_from_postal('11370') == 'Los Barrios' and sources.town_from_postal('11380') == 'Tarifa'
    assert sources.town_from_postal('11330') == 'Jimena' and sources.town_from_postal('11350') == 'Castellar'
    assert sources.town_from_postal('11360') == 'San Roque' and sources.town_from_postal('11340') == 'San Martín del Tesorillo'
    assert sources.town_from_postal('28001') == '' and sources.town_from_postal('11001') == ''
    assert sources.organism_town('Empresa Municipal de Aguas de Algeciras, S.A. (EMALGESA)') == 'Algeciras'
    assert sources.organism_town('Junta de Gobierno Local del Ayuntamiento de Jimena de la Frontera') == 'Jimena'
    assert sources.organism_town('Mancomunidad de Municipios del Campo de Gibraltar') == 'Campo de Gibraltar'
    # Junta que licita una obra en Los Barrios (organismo en Sevilla, lugar por código postal)
    e = {'title': 'Reforma del CEIP Sierra Luna', 'organism': 'Agencia Pública Andaluza de Educación', 'party_city': 'Sevilla',
         'party_zip': '41092', 'place_city': '', 'place_zip': '11370', 'status': 'PUB', 'budget': '850000', 'awarded': '',
         'type': '3', 'deadline': '2026-11-10', 'winner': '', 'updated': NOW.isoformat(), 'url': 'https://x/ceip', 'docs': [], 'folder': 'X1'}
    it = sources.placsp_item(e)
    assert it['town'] == 'Los Barrios' and it['title'].startswith('Licitación:')
    # Un expediente de Valladolid no entra
    assert sources.placsp_item({**e, 'place_zip': '47001', 'party_zip': '47001', 'title': 'Suministro de papel'}) is None
    # Contrato menor
    m = sources.placsp_item({**e, 'status': 'RES', 'awarded': '14900', 'winner': 'Juegos SL'}, menor=True)
    assert m['title'].startswith('Contrato menor:') and m['amount_value'] == 14900.0 and m['tstatus'] == 'MENOR'
    src = db.row("SELECT * FROM sources WHERE kind='placsp' LIMIT 1")
    cid = sources.add_candidate(it, src)
    assert cid
    # La misma licitación desde Gobierto se une; la adjudicación posterior es otra propuesta
    dup = {'title': 'Licitación: Reforma del CEIP Sierra Luna en Los Barrios', 'url': 'https://contratos.gobierto.es/licitaciones/9',
           'published_at': NOW.isoformat(), 'organism': 'Ayuntamiento de Los Barrios'}
    assert sources.add_candidate(dup, {'id': None, 'name': 'Gobierto', 'block': 'Licitaciones', 'kind': 'gobierto', 'priority': 90}) is None
    assert db.row('SELECT COUNT(*) n FROM candidate_links WHERE candidate_id=?', (cid,))['n'] == 1
    adj = sources.placsp_item({**e, 'status': 'ADJ', 'awarded': '799000', 'winner': 'Construcciones Sur SL', 'url': 'https://x/ceip2'})
    cid2 = sources.add_candidate(adj, src)
    assert cid2 and db.row('SELECT tstatus FROM candidates WHERE id=?', (cid2,))['tstatus'] == 'ADJ'


def test_ai_failure_falls_back_and_hides_area():
    from app import pipeline, ai
    from app.ai import AIProviderError
    cid = db.row("SELECT id FROM candidates WHERE tag='licitacion' LIMIT 1")['id']
    old = redaccion.draft
    def boom(*a, **k):
        raise AIProviderError('Gemini no funciona: la clave no es válida.', 'gemini', 400, '', 'auth')
    redaccion.draft = boom
    old_imgs = pipeline.gather_images
    pipeline.gather_images = lambda aid, query=None: None
    try:
        art = pipeline.write_candidate(cid)
    finally:
        redaccion.draft, pipeline.gather_images = old, old_imgs
    assert art and art['headline'] and 'SIN IA' in art['sources_json']
    import os
    os.environ['GEMINI_API_KEY'] = '"GEMINI_API_KEY=AIza 123"'
    from app import config
    assert config._key('GEMINI_API_KEY') == 'AIza123'


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
