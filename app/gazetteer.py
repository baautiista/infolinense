"""Nomenclátor: municipios de España, provincias y comunidades, países y capitales del mundo (de Wikidata).

Sirve para saber si una noticia es de OTRO sitio (Castelldefels, Lima, Nerja…) por el contexto, no por palabras sueltas:
solo cuenta un lugar cuando aparece como lugar («en Nerja», «de Castelldefels», «Ayuntamiento de Rota»).
Se descarga en el servidor y se guarda 30 días; si no se puede, el panel sigue con sus listas propias.
"""
import json
import re
import threading
import time
import unicodedata

import requests

from . import db

WIKIDATA = 'https://query.wikidata.org/sparql'
QUERIES = {
    'es_municipios': 'SELECT DISTINCT ?l WHERE { ?m wdt:P31 wd:Q2074737 ; rdfs:label ?l . FILTER(lang(?l)="es") }',
    'es_provincias': 'SELECT DISTINCT ?l WHERE { { ?m wdt:P31 wd:Q162620 } UNION { ?m wdt:P31 wd:Q10742 } ?m rdfs:label ?l . FILTER(lang(?l)="es") }',
    'paises': 'SELECT DISTINCT ?l WHERE { ?c wdt:P31 wd:Q6256 . { ?c rdfs:label ?l } UNION { ?c wdt:P36 ?k . ?k rdfs:label ?l } FILTER(lang(?l)="es") }',
}
# Nombres que son lugares de La Línea o de su entorno inmediato que no deben contar como «otro sitio»,
# y nombres de municipio que también son palabras corrientes con mayúscula.
_LOCAL = {'la linea', 'la linea de la concepcion', 'gibraltar', 'campo de gibraltar', 'espana', 'andalucia', 'cadiz', 'provincia de cadiz',
          'europa', 'poniente', 'levante', 'santa margarita', 'san bernardo', 'atunara', 'la atunara', 'junquillos', 'el zabal', 'alcaidesa',
          'sobrevela', 'la colonia', 'el conchal', 'san pedro', 'velada', 'la velada'}
_COMMON = {'real', 'santa', 'san', 'villa', 'torre', 'puerto', 'mar', 'sierra', 'valle', 'mesa', 'mesas', 'campo', 'rosal', 'sol', 'luz',
           'paz', 'guardia', 'moral', 'pinos', 'olivar', 'castillo', 'arroyo', 'fuente', 'fuentes', 'molinos', 'navas', 'cuevas', 'casas',
           'barrios', 'llano', 'vega', 'riba', 'pozo', 'puente', 'nueva', 'nuevo', 'centro', 'plaza', 'carrera', 'marina', 'palma', 'gran',
           'cabo', 'linea', 'concepcion', 'victoria', 'aguilar', 'oliva', 'mora', 'rio', 'monte', 'montes', 'lugar', 'senda', 'roda',
           'mayor', 'europa', 'america', 'china', 'chile'}
_names = {'set': set(), 'at': 0.0}
_lock = threading.Lock()


def fold(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def _fetch():
    names = set()
    for key, q in QUERIES.items():
        r = requests.get(WIKIDATA, params={'query': q, 'format': 'json'}, timeout=90,
                         headers={'User-Agent': 'InfoLinenseDesk/1.0 (redaccion local; contacto: alertas@infolinense.com)',
                                  'Accept': 'application/sparql-results+json'})
        r.raise_for_status()
        for b in r.json().get('results', {}).get('bindings', []):
            n = fold(b['l']['value']).strip()
            n = re.sub(r'\s*\(.*?\)', '', n)
            if len(n) >= 4 and n not in _LOCAL and n not in _COMMON:
                names.add(n)
    return names


def load(force=False):
    """Carga el nomenclátor (de la base de datos o, si hace falta, de Wikidata)."""
    with _lock:
        if _names['set'] and not force:
            return _names['set']
        raw = db.get_setting('gazetteer', '')
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if force or not data.get('names') or time.time() - float(data.get('at') or 0) > 30 * 86400:
            try:
                names = _fetch()
                if len(names) > 3000:
                    data = {'at': time.time(), 'names': sorted(names)}
                    db.set_setting('gazetteer', json.dumps(data, ensure_ascii=False))
            except Exception as exc:
                db.log('gazetteer_error', str(exc)[:200])
        _names['set'] = set(data.get('names') or [])
        _names['at'] = time.time()
        return _names['set']


def load_async():
    threading.Thread(target=lambda: load(), daemon=True).start()


_PLACE_CTX = re.compile(r"\b(?:en|de|desde|hacia|para|por|a|al|del|ayuntamiento\s+de|alcalde(?:sa)?\s+de|vecinos\s+de|municipio\s+de|"
                        r"localidad\s+de|ciudad\s+de|pueblo\s+de|provincia\s+de)\s+((?:[A-ZÁÉÍÓÚÑÜ][\wáéíóúñüàèòç'·-]+)(?:\s+(?:de\s+la|de\s+los|de\s+las|del|de|la|el|los|las|d'|i)?\s*[A-ZÁÉÍÓÚÑÜ][\wáéíóúñüàèòç'·-]+){0,3})")


def places_in(text):
    """Lugares (que no son La Línea ni su entorno) que aparecen como lugar en el texto."""
    names = _names['set'] or load()
    if not names:
        return []
    found = []
    for m in _PLACE_CTX.finditer(text or ''):
        words = m.group(1).split()
        for n in range(len(words), 0, -1):  # «Puerto de Santa María», luego «Puerto»…
            cand = fold(' '.join(words[:n]))
            if cand in names and cand not in _LOCAL:
                found.append(' '.join(words[:n]))
                break
    return found
