from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from simulator.api import _bench, _list_algorithms, _list_days, _run_one

router = APIRouter(prefix="/api", tags=["simulator"])


class RunRequest(BaseModel):
    date: str
    ruta: str
    algo: str


class BenchRequest(BaseModel):
    algos: list[str] | None = None
    max_cases: int = Field(default=30, ge=1)
    seed: int = 42
    min_clients: int = Field(default=5, ge=1)


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/algorithms")
def algorithms():
    return {"algorithms": _list_algorithms()}


@router.get("/days")
def days(min_clients: int = 5, head: int = 50):
    return _list_days(min_clients=min_clients, head=head)


@router.post("/run")
def run_one(body: RunRequest):
    try:
        return _run_one(body.date, body.ruta, body.algo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/bench")
def bench(body: BenchRequest):
    try:
        return _bench(
            algos=body.algos or [],
            max_cases=body.max_cases,
            seed=body.seed,
            min_clients=body.min_clients,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
