import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'brands.sqlite'))
for _k, _v in (('RENDER_DIR', 'renders'), ('UPLOAD_DIR', 'uploads'), ('TEMPLATE_DIR', 'templates')):
    os.environ.setdefault(_k, str(Path(_temp.name) / _v))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from app import brands, canva, db, main, publishers, sources, social_publish as sp  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()


class BrandTests(unittest.TestCase):
    def setUp(self):
        db.init_db(); sp.init_tables(); brands.init_tables()
        for t in ('social_accounts', 'candidates', 'articles', 'brand_settings'):
            db.exec_('DELETE FROM ' + t)

    def test_classify_and_source_brand(self):
        self.assertEqual(brands.classify('La Hermandad del Gran Poder anuncia su besamanos'), 'cofrade')
        self.assertEqual(brands.classify('La comparsa linense gana el concurso de agrupaciones del carnaval'), 'carnaval')
        self.assertIsNone(brands.classify('Obras en la calle Real'))
        cid = sources.add_candidate('Besamanos de la Virgen de la Amargura este domingo en La Línea', 'https://x/1', '', 'Google',
                                    published_at=NOW, source_meta={'priority': 70, 'official': 0, 'local_scope': 0})
        self.assertEqual(db.row('SELECT brand FROM candidates WHERE id=?', (cid,))['brand'], 'cofrade')
        cid = sources.add_candidate('Horario de los cultos de la Esperanza', 'https://fb/2', '', 'Amor y Esperanza',
                                    published_at=NOW, source_meta={'priority': 85, 'official': 0, 'local_scope': 1, 'brand': 'cofrade'})
        self.assertEqual(db.row('SELECT brand FROM candidates WHERE id=?', (cid,))['brand'], 'cofrade')
        self.assertTrue(all(r['brand'] == 'cofrade' for r in main.candidates('pending', 'cofrade')))
        self.assertEqual(main.candidates('pending', 'carnaval'), [])

    def test_cofrade_sources_seeded(self):
        names = {r['name'] for r in db.rows("SELECT name FROM sources WHERE brand='cofrade'")}
        self.assertIn('Gran Poder y Ángeles', names); self.assertIn('La Línea Cofrade', names)

    def test_settings_and_templates(self):
        self.assertEqual(canva.template_for('Titular', 'cofrade'), 'EAHXQHp9uXw')  # plantilla publicada por defecto
        b = brands.save_settings('carnaval', {'templates': {'main': 'https://www.canva.com/brand/brand-templates/EAHcarnav12'},
                                              'sections': 'Agrupaciones, Concurso'})
        self.assertEqual(b['templates']['main'], 'EAHcarnav12')
        self.assertEqual(b['sections'], ['Agrupaciones', 'Concurso'])
        f = canva.design_fields({'brand': 'carnaval', 'headline': 'Final', 'subtitle': 'Sábado', 'section': 'concurso'})
        self.assertEqual((f['SECTION'], f['template'], f['page']), ('Concurso', 'EAHcarnav12', 1))
        with self.assertRaises(ValueError):
            brands.save_settings('carnaval', {'templates': {'main': 'no es una plantilla'}})

    def test_cofrade_pages_by_hermandad(self):
        page = lambda sec, **kw: canva.design_fields(dict({'brand': 'cofrade', 'headline': 'H', 'subtitle': 'S', 'section': sec}, **kw))
        self.assertEqual(page('Entrada Triunfal y Alegría')['page'], 1)
        self.assertEqual(page('GRAN PODER')['page'], 8)
        self.assertEqual(page('Hermandad del Rocío')['page'], 16)
        g = page('General', section_label='Consejo de Hermandades')
        self.assertEqual((g['page'], g['SECTION']), (17, 'Consejo de Hermandades'))
        self.assertEqual(page('Ocasiones especiales')['page'], 18)
        self.assertEqual(page('algo raro')['page'], 17)  # sin sección conocida: General
        self.assertEqual(canva._page_for({'brand': 'cofrade', 'section': 'Gran Poder y Ángeles', '_carousel': True}), 19)
        self.assertEqual(brands.find_section('cofrade', 'Esperanza y Concepción (Silencio)'), 'Esperanza y Concepción')
        self.assertEqual(brands.find_section('cofrade', 'Misericordia y Amargura'), 'Misericordia y Amargura')

    def test_text_size_variants(self):
        schema = {k: {'type': 'text'} for k in ('HEADLINE', 'HEADLINE_L', 'HEADLINE_S', 'SECTION', 'SECTION_S')}
        v = canva.size_variants(schema, {'HEADLINE': 'Corto', 'SECTION': 'Esperanza y Concepción'})
        self.assertEqual((v['HEADLINE_L']['text'], v['HEADLINE']['text'], v['HEADLINE_S']['text']), ('Corto', ' ', ' '))
        self.assertEqual((v['SECTION_S']['text'], v['SECTION']['text']), ('Esperanza y Concepción', ' '))
        v = canva.size_variants(schema, {'HEADLINE': 'x' * 90, 'SECTION': 'Rocío'})
        self.assertEqual(v['HEADLINE_S']['text'], 'x' * 90)
        self.assertEqual(v['SECTION']['text'], 'Rocío')
        self.assertEqual(canva.size_variants({'HEADLINE': {'type': 'text'}}, {'HEADLINE': 'x'}), {})  # plantilla sin variantes

    def test_hermandad_detected_from_headline(self):
        f = canva.design_fields({'brand': 'cofrade', 'headline': 'La Amargura saldrá desde su casa hermandad en la Semana Santa de 2027',
                                 'subtitle': '', 'section': 'SEMANA SANTA'})
        self.assertEqual((f['SECTION'], f['page']), ('Misericordia y Amargura', 12))
        self.assertEqual(brands.detect_section('cofrade', 'El Cautivo de Medinaceli estrena túnica'), 'Cautivo y Trinidad')
        self.assertEqual(brands.detect_section('cofrade', 'La Banda Santa Bárbara graba su primer disco'), '')

    def test_general_label_defaults_to_holy_week(self):
        f = canva.design_fields({'brand': 'cofrade', 'headline': 'H', 'subtitle': '', 'section': 'General'})
        self.assertTrue(f['SECTION'].startswith('Semana Santa 20'))
        db.exec_("INSERT OR REPLACE INTO brand_settings(slug,data) VALUES('cofrade',?)",
                 ('{"sections": ["HERMANDADES", "SEMANA SANTA", "CULTOS", "GLORIAS", "PATRIMONIO", "AGENDA"]}',))
        self.assertEqual(brands.settings('cofrade')['sections'][0], 'Entrada Triunfal y Alegría')

    def test_accounts_are_separate_per_brand(self):
        with sp.use_brand('cofrade'):
            sp._save_account('instagram', {'access_token': 'C', 'username': 'elcofradelinense', 'expires_at': 9e12, 'obtained_at': 9e12})
        self.assertEqual(sp.connected_networks('cofrade'), ['instagram'])
        self.assertEqual(sp.connected_networks('infolinense'), [])
        self.assertEqual(main.networks_status('cofrade')['instagram']['name'], 'elcofradelinense')
        # el estado de OAuth recuerda el medio
        with sp.use_brand('carnaval'):
            state = sp._new_state('tiktok')
        self.assertEqual(sp._check_state('tiktok', state), 'carnaval')

    def test_brand_web_from_railway_prefix(self):
        self.assertFalse(brands.web_enabled('carnaval'))
        with patch.dict(os.environ, {'CARNAVAL_WEBHOOK_URL': 'https://carnaval.example/fn', 'CARNAVAL_WEBHOOK_SECRET': 'k'}):
            self.assertTrue(brands.web_enabled('carnaval'))
            sent = {}

            class R:
                ok = True; status_code = 200; text = ''
                def json(self): return {'url': 'https://carnaval.example/n/1'}
            with patch.object(publishers.requests, 'post', side_effect=lambda url, json=None, headers=None, timeout=None: sent.update(url=url, json=json, headers=headers) or R()):
                url = publishers.publish({'id': 3, 'brand': 'carnaval', 'headline': 'Comparsa', 'subtitle': 'x', 'body': 'y', 'section': 'concurso'})
        self.assertEqual(url, 'https://carnaval.example/n/1')
        self.assertEqual((sent['url'], sent['json']['brand'], sent['json']['section'], sent['headers']['x-infolinense-secret']),
                         ('https://carnaval.example/fn', 'carnaval', 'CONCURSO', 'k'))

    def test_caption_uses_brand_hashtags(self):
        with sp.use_brand('cofrade'):
            self.assertIn('#ElCofradeLinense', sp.caption({'headline': 'H', 'subtitle': '', 'body': 'B'}))


if __name__ == '__main__':
    unittest.main()
