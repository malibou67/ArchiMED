"""Extraction d'un PAGE-XML pour l'indexation — code exécuté dans les process du pool.

Module **volontairement minuscule et sans import hors stdlib**. Sous Windows le pool démarre
en *spawn* : chaque worker réimporte le module de la fonction soumise. La loger dans
`services` ferait rejouer tout son init (résolution de DATA_DIR, rapidfuzz, scan_snapshot)
dans chacun des process, à chaque indexation.

Les fonctions sont au niveau module, donc picklables par référence (même contrainte que
`_ocr_worker_page` dans `ocr_service`).
"""
import xml.etree.ElementTree as ET

# Espace de noms PAGE (PRImA), celui qu'écrit le pipeline OCR.
NS = {'ns': 'http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15'}


def extraire_rectangle(coords: str) -> str:
    points = [tuple(map(int, c.split(','))) for c in coords.split()]
    x_min = min(p[0] for p in points)
    x_max = max(p[0] for p in points)
    y_min = min(p[1] for p in points)
    y_max = max(p[1] for p in points)
    return f"({x_min}, {y_min}), ({x_max}, {y_max})"


def nettoyer_texte(texte: str):
    for char in ['&quot', '?', '¬', ':', '(', ')', ',', '.', '_', ';', '█', '/', '+', '*', '--']:
        texte = texte.replace(char, ' ')
    texte = texte.replace('  ', ' ').replace("&#x27", "'").lower()
    return [mot.strip('-') for mot in texte.split() if mot.isalpha()]


def extract_page(job: tuple) -> tuple:
    """`(idx, path_str, page_name)` → `(idx, page_name, [(mot, coords), …], ok)`.

    Renvoie les couples `(mot, coords)` et non les chaînes d'occurrence formatées : le nom de
    page est le même pour toute la liste, l'envoyer une seule fois allège nettement le tube,
    et le format `"{page} - {coords}"` — dont dépendent `_purge_registres` et `get_word_pages`
    — reste écrit à un seul endroit, dans `services`.

    **Ne lève jamais** : `ok=False` signale une page perdue (XML tronqué, lecture réseau
    interrompue, format inattendu). Le compte est tenu par la boucle appelante, qui le
    journalise une fois en fin de run plutôt que page par page.
    """
    idx, path_str, page_name = job
    pairs = []
    try:
        root = ET.parse(path_str).getroot()
        for ligne in root.findall(".//ns:TextLine", NS):
            for mot_element in ligne.findall(".//ns:Word", NS):
                texte_unicode = mot_element.find("ns:TextEquiv/ns:Unicode", NS)
                if texte_unicode is not None and texte_unicode.text:
                    coords_elem = mot_element.find("ns:Coords", NS)
                    mot_coords = extraire_rectangle(coords_elem.attrib['points']) if coords_elem is not None else ""
                    for mot in nettoyer_texte(texte_unicode.text):
                        pairs.append((mot, mot_coords))
    except Exception:
        return idx, page_name, [], False
    return idx, page_name, pairs, True
