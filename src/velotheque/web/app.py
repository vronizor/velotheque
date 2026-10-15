from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from velotheque.db import get_engine
from velotheque.strava.sync import mark_interrupted
from velotheque.web import explore, jobs, ride, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    mark_interrupted(get_engine())
    yield
    jobs.executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Vélothèque", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
for module in (explore, ride, settings, jobs):
    app.include_router(module.router)
