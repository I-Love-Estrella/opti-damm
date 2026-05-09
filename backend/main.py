from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.data_layer import DataLayer
from backend.database import Base, engine
from backend.factory import seed
from backend.routers.routes import router as routes_router
from backend.routers.clients import router as clients_router
from backend.routers.pdfs import router as pdfs_router
from backend.routers.simulator import router as simulator_router


def _allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "https://optidamm.ink",
        "https://www.optidamm.ink",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DATABASE_ON_STARTUP", "").lower() in {"1", "true", "yes"}:
        seed()
    app.state.dl = DataLayer()
    yield


app = FastAPI(title="Damm Smart Truck API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_origin_regex=os.getenv("ALLOWED_ORIGIN_REGEX"),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_router)
app.include_router(clients_router)
app.include_router(pdfs_router)
app.include_router(simulator_router)


@app.get("/health")
def health():
    return {"status": "ok"}
