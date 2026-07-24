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
async def _resume_tasks():
    """Recharge la file de tâches : marque les tâches interrompues et reprend les attentes."""
    from task_service import TaskService
    TaskService.load_on_startup()


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
    import uvicorn
    import webbrowser
    import threading
    import urllib.request
    import pystray
    from PIL import Image, ImageDraw

    # PyInstaller sans console : stdout/stderr sont None, uvicorn plante sur isatty()
    if getattr(sys, 'frozen', False):
        log_path = os.path.join(os.path.dirname(sys.executable), 'archiMED.log')
        _log_file = open(log_path, 'w', encoding='utf-8')
        sys.stdout = _log_file
        sys.stderr = _log_file

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 38520))
    url = f"http://{host}:{port}"

    # Instance déjà active → ouvrir le navigateur et quitter
    try:
        urllib.request.urlopen(f"{url}/health", timeout=1)
        webbrowser.open(url)
        sys.exit(0)
    except Exception:
        pass

    # Uvicorn dans un thread daemon
    threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port, log_config=None),
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
        icon.stop()
        # os._exit saute les handlers atexit : sans ce nettoyage, les workers d'un OCR en
        # cours survivent à la fermeture et continuent d'occuper le GPU.
        try:
            import ocr_service
            ocr_service.kill_active_pools()
        except Exception:
            pass
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
