"""Statistiques d'index et journal des recherches.

Séparé de services.py (déjà volumineux). Tout est calculé à la volée depuis
index.json via le cache de IndexesService._load_index, puis mis en cache par
(index_id, mtime) — même stratégie que le vocabulaire.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from services import IndexesService, CollectionsService, TranscriptionsService


def _median_year(periode: Any) -> Optional[int]:
    """Année représentative d'un registre : médiane de sa période [début, fin].
    Une seule borne renseignée → cette borne. Aucune → None (registre exclu)."""
    start = end = None
    try:
        if periode and periode[0]:
            start = int(periode[0])
        if periode and len(periode) > 1 and periode[1]:
            end = int(periode[1])
    except (ValueError, TypeError):
        return None
    if start is not None and end is not None:
        return (start + end) // 2
    return start if start is not None else end


def _decade(year: int) -> int:
    """Groupement temporel par année (l'axe des graphiques n'étiquette que tous les 5 ans)."""
    return year


def _year_span(periode: Any) -> Optional[tuple]:
    """Intervalle d'années [début, fin] couvert par un registre, bornes incluses.
    Une seule borne renseignée → année unique. Aucune → None (registre exclu)."""
    start = end = None
    try:
        if periode and periode[0]:
            start = int(periode[0])
        if periode and len(periode) > 1 and periode[1]:
            end = int(periode[1])
    except (ValueError, TypeError):
        return None
    if start is None and end is None:
        return None
    if start is None:
        start = end
    if end is None:
        end = start
    return (start, end) if start <= end else (end, start)


class IndexStatsService:
    # index_id -> (mtime, computed) ; invalidé quand index.json change.
    _stats_cache: Dict[str, tuple] = {}

    FREQ_BUCKETS = [(1, 1, "1"), (2, 5, "2-5"), (6, 20, "6-20"),
                    (21, 100, "21-100"), (101, 1000, "101-1000"),
                    (1001, float('inf'), ">1000")]

    @staticmethod
    def _compute(index_id: str) -> Optional[Dict]:
        """Agrégats par index, construits en un seul passage sur les occurrences :
        stats par registre, qualité, distribution de fréquences, décennies."""
        index_file = IndexesService.get_indexes_dir() / index_id / "index.json"
        if not index_file.exists():
            IndexStatsService._stats_cache.pop(index_id, None)
            return None
        mtime = index_file.stat().st_mtime
        cached = IndexStatsService._stats_cache.get(index_id)
        if cached and cached[0] == mtime:
            return cached[1]

        loaded = IndexesService._load_index(index_id)
        if loaded is None:
            return None
        total_unique, base_entries, words, registres_map = loaded

        sorted_folders = sorted(registres_map.keys(), key=len, reverse=True)
        folder_of_page: Dict[str, Optional[str]] = {}

        # Par registre : pages (set), occurrences, mots uniques
        reg_pages: Dict[Optional[str], set] = {}
        reg_occs: Dict[Optional[str], int] = {}
        reg_unique: Dict[Optional[str], int] = {}

        total_occurrences = 0
        short_unique = short_occs = 0   # mots de 1-2 lettres (bruit OCR probable)
        hapax_unique = 0                # mots vus une seule fois
        bucket_counts = [0] * len(IndexStatsService.FREQ_BUCKETS)
        all_pages: set = set()

        for word, occs in words.items():
            n = len(occs)
            total_occurrences += n
            if len(word) <= 2:
                short_unique += 1
                short_occs += n
            if n == 1:
                hapax_unique += 1
            for i, (lo, hi, _label) in enumerate(IndexStatsService.FREQ_BUCKETS):
                if lo <= n <= hi:
                    bucket_counts[i] += 1
                    break

            word_folders: set = set()
            for occ in occs:
                page = occ.split(' - ')[0]
                folder = folder_of_page.get(page)
                if page not in folder_of_page:
                    folder = IndexesService._get_registre_folder(page, sorted_folders)
                    folder_of_page[page] = folder
                all_pages.add(page)
                reg_pages.setdefault(folder, set()).add(page)
                reg_occs[folder] = reg_occs.get(folder, 0) + 1
                word_folders.add(folder)
            for f in word_folders:
                reg_unique[f] = reg_unique.get(f, 0) + 1

        # Liste par registre (folder None = pages non rattachées à un registre connu)
        registres = []
        for folder in sorted(reg_pages.keys(), key=lambda f: -(reg_occs.get(f, 0))):
            info = registres_map.get(folder, {}) if folder else {}
            registres.append({
                "folder": folder or "(non rattaché)",
                "titre": info.get("titre"),
                "collection": info.get("collection_titre"),  # multi-sources : d'où vient le registre
                "periode": info.get("periode"),
                "pages": len(reg_pages[folder]),
                "occurrences": reg_occs.get(folder, 0),
                "unique_words": reg_unique.get(folder, 0),
                "median_year": _median_year(info.get("periode")) if folder else None,
            })

        # Pages indexées par décennie (registres rattachés à l'année médiane de leur période)
        decade_pages: Dict[int, int] = {}
        decade_regs: Dict[int, int] = {}
        registres_without_periode = []
        for reg in registres:
            if reg["folder"] == "(non rattaché)":
                continue
            year = reg["median_year"]
            if year is None:
                registres_without_periode.append(reg["folder"])
                continue
            d = _decade(year)
            decade_pages[d] = decade_pages.get(d, 0) + reg["pages"]
            decade_regs[d] = decade_regs.get(d, 0) + 1
        pages_by_decade = [
            {"decade": d, "pages": decade_pages[d], "registres": decade_regs[d]}
            for d in sorted(decade_pages)
        ]

        computed = {
            "totals": {
                "total_unique_words": total_unique,
                "total_word_occurrences": total_occurrences,
                "total_pages": len(all_pages),
                "registres_count": len([r for r in registres if r["folder"] != "(non rattaché)"]),
            },
            "frequency_distribution": [
                {"bucket": label, "words": bucket_counts[i]}
                for i, (_lo, _hi, label) in enumerate(IndexStatsService.FREQ_BUCKETS)
            ],
            "registres": registres,
            "pages_by_decade": pages_by_decade,
            "short_words": {"unique": short_unique, "occurrences": short_occs},
            "hapax": {"unique": hapax_unique},
            "registres_without_periode": registres_without_periode,
            "folder_of_page": folder_of_page,  # interne (term-frequency), retiré des réponses
        }
        IndexStatsService._stats_cache[index_id] = (mtime, computed)
        return computed

    @staticmethod
    def get_corpus_stats(index_id: str, top: int = 20) -> Optional[Dict]:
        computed = IndexStatsService._compute(index_id)
        if computed is None:
            return None
        loaded = IndexesService._load_index(index_id)
        if loaded is None:
            return None
        _total, base_entries, _words, registres_map = loaded

        # Top mots significatifs : > 2 lettres et hors mots-outils
        top_words = sorted(
            (e for e in base_entries
             if len(e["word"]) > 2 and e["word"] not in IndexesService.STOP_WORDS),
            key=lambda e: -e["occurrences"],
        )[:top]

        meta = IndexesService.get_index(index_id) or {}
        stats = meta.get("stats") or {}
        totals = dict(computed["totals"])
        totals["year_min"] = stats.get("year_min")
        totals["year_max"] = stats.get("year_max")

        return {
            "totals": totals,
            "top_words": top_words,
            "frequency_distribution": computed["frequency_distribution"],
            "registres": [
                {k: v for k, v in r.items() if k != "median_year"}
                for r in computed["registres"]
            ],
            "pages_by_decade": computed["pages_by_decade"],
        }

    @staticmethod
    def get_term_frequency(index_id: str, terms: List[str],
                           fuzzy_threshold: Optional[int] = None) -> Optional[Dict]:
        """Occurrences de chaque terme par décennie (style Ngram). Chaque registre est
        rattaché à l'année médiane de sa période ; sans période il est exclu (compté
        dans excluded_registres). Normalisation per_1000_pages fournie par série."""
        computed = IndexStatsService._compute(index_id)
        if computed is None:
            return None
        loaded = IndexesService._load_index(index_id)
        if loaded is None:
            return None
        _total, _base, words, registres_map = loaded

        folder_of_page = computed["folder_of_page"]
        # folder -> décennie (None si période absente)
        folder_decade: Dict[str, Optional[int]] = {}
        for folder, info in registres_map.items():
            year = _median_year(info.get("periode"))
            folder_decade[folder] = _decade(year) if year is not None else None

        pages_per_decade = {d["decade"]: d["pages"] for d in computed["pages_by_decade"]}

        terms = [t.strip().lower() for t in terms if t.strip()][:5]
        series = []
        decades_seen: set = set()
        for term in terms:
            stem = IndexesService._term_stem(term, fuzzy_threshold)
            occ_by_decade: Dict[int, int] = {}
            page_sets: Dict[int, set] = {}
            for word, occs in words.items():
                if not IndexesService._term_matches(term, word, fuzzy_threshold, stem):
                    continue
                for occ in occs:
                    page = occ.split(' - ')[0]
                    folder = folder_of_page.get(page)
                    if folder is None:
                        continue
                    d = folder_decade.get(folder)
                    if d is None:
                        continue
                    occ_by_decade[d] = occ_by_decade.get(d, 0) + 1
                    page_sets.setdefault(d, set()).add(page)
            decades_seen |= set(occ_by_decade)
            series.append({"term": term, "occ_by_decade": occ_by_decade,
                           "pages_by_decade": {d: len(s) for d, s in page_sets.items()}})

        # Axe commun : toutes les décennies du corpus (séries à 0 incluses pour le contexte)
        decades = sorted(set(pages_per_decade) | decades_seen)
        out_series = []
        for s in series:
            points = []
            for d in decades:
                occ = s["occ_by_decade"].get(d, 0)
                denom = pages_per_decade.get(d, 0)
                points.append({
                    "decade": d,
                    "occurrences": occ,
                    "pages": s["pages_by_decade"].get(d, 0),
                    "per_1000_pages": round(occ / denom * 1000, 2) if denom else None,
                })
            out_series.append({"term": s["term"], "points": points})

        excluded = sum(1 for d in folder_decade.values() if d is None)
        return {
            "terms": [s["term"] for s in series],
            "decades": decades,
            "series": out_series,
            "fuzzy_threshold": fuzzy_threshold,
            "excluded_registres": excluded,
        }

    @staticmethod
    def get_quality_stats(index_id: str) -> Optional[Dict]:
        computed = IndexStatsService._compute(index_id)
        if computed is None:
            return None
        totals = computed["totals"]
        unique = totals["total_unique_words"] or 1
        occs = totals["total_word_occurrences"] or 1
        short = computed["short_words"]
        hapax = computed["hapax"]

        meta = IndexesService.get_index(index_id) or {}
        coverage = meta.get("coverage")
        indexed_by_folder = {r["folder"]: r["pages"] for r in computed["registres"]}

        per_registre = []
        ocr_total: Optional[int] = None
        empty_total: Optional[int] = None
        if coverage is not None:
            ocr_total = sum(coverage.values())
            empty_total = 0
            for folder, n_ocr in sorted(coverage.items()):
                n_indexed = indexed_by_folder.get(folder, 0)
                empty = max(0, n_ocr - n_indexed)
                empty_total += empty
                per_registre.append({
                    "folder": folder,
                    "pages_indexed": n_indexed,
                    "pages_ocr": n_ocr,
                    "empty_pages": empty,
                })
            per_registre.sort(key=lambda r: -r["empty_pages"])

        return {
            "short_words": {
                "unique": short["unique"],
                "occurrences": short["occurrences"],
                "ratio_unique": round(short["unique"] / unique, 4),
                "ratio_occurrences": round(short["occurrences"] / occs, 4),
            },
            "hapax": {
                "unique": hapax["unique"],
                "ratio_unique": round(hapax["unique"] / unique, 4),
            },
            "pages": {
                "indexed": totals["total_pages"],
                "ocr_total": ocr_total,
                "empty": empty_total,
                "coverage_known": coverage is not None,
            },
            "registres_without_periode": computed["registres_without_periode"],
            "per_registre": per_registre,
        }


class CollectionStatsService:
    """Statistiques d'avancement d'une collection : volume scanné, couverture OCR
    par modèle, répartition des pages par décennie, couverture transcription.

    Tout est agrégé à la volée depuis les métadonnées de la collection (registres +
    ocr_status, déjà reconstruites au scan) et le résumé des transcriptions. Calcul
    léger (pas de parsing de fichiers lourds) → pas de cache."""

    @staticmethod
    def _year_bounds(periode: Any) -> tuple:
        """(début, fin) d'une période [début, fin] sous forme d'entiers, None si absent."""
        start = end = None
        try:
            if periode and periode[0]:
                start = int(periode[0])
            if periode and len(periode) > 1 and periode[1]:
                end = int(periode[1])
        except (ValueError, TypeError):
            pass
        return start, end

    @staticmethod
    def get_stats(collection_id: str) -> Optional[Dict]:
        col = CollectionsService.get_collection(collection_id)
        if col is None:
            return None

        registres = col.get("registres") or []
        folder = col.get("folder_name") or col.get("id")

        # Résumé des transcriptions de cette collection (par registre / modèle)
        trans_by_registre: Dict[str, Dict[str, int]] = {}
        trans_totals: Dict[str, int] = {}
        for entry in TranscriptionsService.get_summary():
            if entry.get("collection_id") in (folder, col.get("id")):
                for reg in entry.get("registres", []):
                    trans_by_registre[reg["registre_id"]] = reg.get("counts", {})
                trans_totals = entry.get("totals", {}) or {}
                break

        # Bornes d'années : période de la collection, sinon dérivées des registres
        year_min, year_max = CollectionStatsService._year_bounds(col.get("periode"))

        pages_total = 0
        ocr_done: Dict[str, int] = {}
        ocr_total: Dict[str, int] = {}
        year_pages: Dict[int, int] = {}
        registres_without_periode: List[str] = []
        registre_stats: List[Dict] = []

        for reg in registres:
            reg_folder = reg.get("folder_name") or reg.get("id") or ""
            pages = int(reg.get("pages_count") or 0)
            pages_total += pages

            # Couverture OCR par modèle pour ce registre
            ocr_status = reg.get("ocr_status") or {}
            reg_ocr: Dict[str, Dict] = {}
            for model, st in ocr_status.items():
                done = int((st or {}).get("pages_done") or 0)
                total = int((st or {}).get("pages_total") or 0) or pages
                ocr_done[model] = ocr_done.get(model, 0) + done
                ocr_total[model] = ocr_total.get(model, 0) + total
                reg_ocr[model] = {
                    "model": model,
                    "pages_done": done,
                    "pages_total": total,
                    "coverage": (done / total) if total else 0.0,
                }

            # Pages réparties sur chaque année couverte par la période du registre
            span = _year_span(reg.get("periode"))
            if span is None:
                if reg_folder:
                    registres_without_periode.append(reg_folder)
            else:
                start, end = span
                years = range(start, end + 1)
                base, rem = divmod(pages, end - start + 1)
                for i, y in enumerate(years):
                    year_pages[y] = year_pages.get(y, 0) + base + (1 if i < rem else 0)
                # Compléter les bornes globales si la collection n'a pas de période
                if year_min is None or start < year_min:
                    year_min = start
                if year_max is None or end > year_max:
                    year_max = end

            registre_stats.append({
                "folder_name": reg_folder,
                "titre": reg.get("titre"),
                "periode": reg.get("periode"),
                "pages_count": pages,
                "ocr": reg_ocr,
                "transcriptions": trans_by_registre.get(reg_folder, {}),
            })

        # Tri des registres par volume de pages décroissant
        registre_stats.sort(key=lambda r: -r["pages_count"])

        ocr_by_model = [
            {
                "model": m,
                "pages_done": ocr_done[m],
                "pages_total": ocr_total.get(m, 0),
                "coverage": (ocr_done[m] / ocr_total[m]) if ocr_total.get(m) else 0.0,
            }
            for m in sorted(ocr_done, key=lambda m: -ocr_done[m])
        ]

        # Axe continu : toutes les années entre la première et la dernière scannée
        pages_by_year = (
            [{"year": y, "pages": year_pages.get(y, 0)}
             for y in range(min(year_pages), max(year_pages) + 1)]
            if year_pages else []
        )

        transcriptions_by_model = [
            {"model": m, "files": trans_totals[m]}
            for m in sorted(trans_totals, key=lambda m: -trans_totals[m])
        ]

        return {
            "id": col.get("id"),
            "titre": col.get("titre"),
            "totals": {
                "registres_count": len(registres),
                "pages_total": pages_total,
                "year_min": year_min,
                "year_max": year_max,
                "ocr_models_count": len(ocr_by_model),
            },
            "ocr_by_model": ocr_by_model,
            "pages_by_year": pages_by_year,
            "transcriptions_by_model": transcriptions_by_model,
            "transcriptions_grand_total": sum(trans_totals.values()),
            "registres": registre_stats,
            "registres_without_periode": registres_without_periode,
        }
