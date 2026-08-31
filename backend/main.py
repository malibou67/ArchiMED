import multiprocessing
import os
import sys

# PyInstaller : les process du pool OCR sont relancés via ArchiMED.exe et réexécutent donc
# ce script. freeze_support() doit être appelé AVANT tout le reste — il détourne le fils
# vers le code worker multiprocessing et ne rend jamais la main. Sans lui, le fils
# redémarre l'application entière (log tronqué, onglet navigateur, sortie immédiate) et le
# pool casse → repli séquentiel, un seul cœur utilisé dans le build.
# Hors gel (dev), l'appel est un no-op.
if getattr(sys, 'frozen', False) and sys.stdout is None:
    # Build sans console : stdout/stderr valent None ; un print d'une bibliothèque dans un
    # worker planterait le process avant même l'initialiseur du pool.
    sys.stdout = sys.stderr = open(os.devnull, 'w')
multiprocessing.freeze_support()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

# Charger le .env AVANT d'importer les routers : ocr_service lit OCR_WORKERS /
# OCR_MIXED_PRECISION au moment de l'import.
load_dotenv()

# …et avant tout code applicatif : le journal doit être en place pour capter ce que les
# imports eux-mêmes émettent. Au niveau module (et non dans `__main__`) pour couvrir aussi le
# lancement via `uvicorn main:app` en développement.
import app_logging
_log_file = app_logging.setup()
log = app_logging.get_logger('app')

from routers import models, collections, registres, transcriptions, indexes, system, ocr, settings, tasks
# Importer les modules de runners enregistre leurs types auprès du moteur de tâches
# (ocr_service → 'ocr', index_runner → 'index') AVANT la reprise au démarrage.
import ocr_service  # noqa: F401  (enregistre le runner OCR)
import index_runner  # noqa: F401  (enregistre le runner d'indexation)

app = FastAPI(
    title="ArchiMED OCR API",
    description="API pour la gestion des collections OCR et des modèles",
    version="1.0.0"
)

app.include_router(models.router, prefix="/api/models", tags=["Models"])
app.include_router(collections.router, prefix="/api/collections", tags=["Collections"])
app.include_router(registres.router, prefix="/api/registres", tags=["Registres"])
app.include_router(transcriptions.router, prefix="/api/transcriptions", tags=["Transcriptions"])
app.include_router(indexes.router, prefix="/api/indexes", tags=["Indexes"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])


@app.on_event("startup")
async def _on_startup():
    """Ouvre le journal du poste, contrôle le stockage, puis recharge la file de tâches
    (marque les tâches interrompues et reprend les attentes)."""
    import machine_identity
    from services import DATA_DIR, check_data_storage
    from app_logging import kv
    from task_service import TaskService

    ident = machine_identity.get_identity()
    log.info("Démarrage " + kv(
        version=app.version,
        poste=ident['machine_id'],
        libellé=ident['machine_label'],
        opérateur=ident['operator'],
        data=DATA_DIR,
        journal=_log_file,
        mode='exe' if getattr(sys, 'frozen', False) else 'dev',
        python=sys.version.split()[0],
    ))

    storage = check_data_storage()
    if not storage['exists']:
        log.error(f"Dossier de données introuvable {kv(data=storage['data_dir'])}")
    elif not storage['writable']:
        log.error(f"Dossier de données en lecture seule {kv(data=storage['data_dir'])}")

    TaskService.load_on_startup()
    # Après la reprise seulement : les tâches interrompues viennent d'être rétablies, on ne
    # signalera donc que les reconstructions dont plus aucune tâche ne s'occupe.
    index_runner.reconcile_orphan_builds()


@app.get("/health")
async def health():
    return {"status": "ok"}

def _get_static_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_static_dir = _get_static_dir()

if os.path.isdir(_static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # Laisser passer les routes /api/* (déjà gérées par les routers)
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        file_path = os.path.join(_static_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))

if __name__ == "__main__":
    import logging
    import uvicorn
    import webbrowser
    import threading
    import urllib.request
    import pystray
    from PIL import Image, ImageDraw

    # PyInstaller sans console : stdout/stderr sont None, uvicorn plante sur isatty(). La
    # redirection va dans le journal du poste : tracebacks de threads et impressions des
    # bibliothèques tierces y sont datés et rangés avec le reste, au lieu d'un fichier brut
    # écrit à côté de l'exe — donc sur le NAS, donc partagé avec tous les autres postes.
    if getattr(sys, 'frozen', False):
        app_logging.redirect_std_streams()

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 38520))
    url = f"http://{host}:{port}"

    # Instance déjà active → ouvrir le navigateur et quitter
    try:
        urllib.request.urlopen(f"{url}/health", timeout=1)
        log.info("Instance déjà active, ouverture du navigateur")
        webbrowser.open(url)
        sys.exit(0)
    except Exception:
        pass

    # Uvicorn dans un thread daemon. `log_config=None` laisse ses journaux remonter au handler
    # racine ; `access_log=False` les garde exploitables : l'UI interroge /api/tasks chaque
    # seconde, une ligne par requête noierait tout le reste.
    threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port, log_config=None, access_log=False),
        daemon=True
    ).start()

    # Ouvrir le navigateur après démarrage du serveur
    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    # Icône systray : cercle bleu avec "A"
    def _make_icon() -> Image.Image:
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2, 2, size - 2, size - 2], fill=(25, 118, 210, 255))
        draw.text((size // 2, size // 2), "A", fill="white", anchor="mm")
        return img

    def _on_open(icon, item):
        webbrowser.open(url)

    def _on_quit(icon, item):
        log.info("Arrêt demandé depuis la zone de notification")
        icon.stop()
        # os._exit saute les handlers atexit : sans ce nettoyage, les workers d'un OCR ou
        # d'une indexation en cours survivent à la fermeture et continuent d'occuper le GPU
        # et le CPU.
        try:
            import pool_registry
            pool_registry.kill_active_pools()
        except Exception as e:
            log.error(f"Échec de l'arrêt des workers : {e}")
        logging.shutdown()
        os._exit(0)

    tray = pystray.Icon(
        name="ArchiMED",
        icon=_make_icon(),
        title="ArchiMED — clic droit pour quitter",
        menu=pystray.Menu(
            pystray.MenuItem("Ouvrir ArchiMED", _on_open, default=True),
            pystray.MenuItem("Quitter", _on_quit),
        ),
    )
    tray.run()
