from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from velotheque.config import get_settings
from velotheque.db import get_engine
from velotheque.photos import PHOTO_DIR
from velotheque.strava.sync import mark_interrupted
from velotheque.web import explore, jobs, ride, route, settings, views


@asynccontextmanager
async def lifespan(app: FastAPI):
    mark_interrupted(get_engine())
    (get_settings().data_dir / PHOTO_DIR).mkdir(parents=True, exist_ok=True)
    yield
    jobs.executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Vélothèque", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.mount(
    "/photos",
    StaticFiles(directory=get_settings().data_dir / PHOTO_DIR, check_dir=False),
    name="photos",
)
for module in (explore, ride, route, settings, jobs, views):
    app.include_router(module.router)
