from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from simulator.api import _bench, _list_algorithms, _list_days, _run_one

router = APIRouter(prefix="/copilot", tags=["copilot"])


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str


class ChatRequest(BaseModel):
    message: str
    messages: list[ChatMessage] = Field(default_factory=list)
    frontend_context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str


SYSTEM_PROMPT = """You are the DDI Smart Truck dispatcher co-pilot.
Answer operational questions about truck routes, clients, loading, KPIs, and simulations.
Use tools whenever the answer depends on backend data. You also receive current frontend
context, which may include selected route, selected stop, load mode, truck type, metrics,
visible stops, pallets, and system log. Treat frontend context as current UI state, and
backend tools as source-of-truth for route/order/client/simulator data.

Keep answers concise and actionable. Mention route IDs, client names, quantities, windows,
or algorithm names when relevant. If a requested action is outside the available tools,
say what data you can inspect and what would still require an operator action."""


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "get_frontend_context",
        "description": "Returns the current dispatcher-console UI state sent by the browser.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_routes",
        "description": "Lists available delivery routes by date and route code.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_route_detail",
        "description": "Gets route detail, truck, driver, transports, client orders, volumes, weights, and returnables.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Route date in YYYY-MM-DD format."},
                "ruta": {"type": "string", "description": "Route code, for example DR0027."},
            },
            "required": ["date", "ruta"],
        },
    },
    {
        "name": "list_clients",
        "description": "Lists clients. Use query to filter by client id, name, address, postal code, or city.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional case-insensitive search text."},
                "limit": {"type": "integer", "description": "Maximum number of clients to return, default 20."},
            },
        },
    },
    {
        "name": "get_client",
        "description": "Gets one client by id, including coordinates and delivery time windows.",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Client identifier."},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "list_algorithms",
        "description": "Lists route simulation algorithms available in the backend.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_days",
        "description": "Lists route/day cases that can be simulated.",
        "parameters": {
            "type": "object",
            "properties": {
                "min_clients": {"type": "integer", "description": "Minimum clients per route, default 5."},
                "head": {"type": "integer", "description": "Maximum rows to return, default 20."},
            },
        },
    },
    {
        "name": "run_simulation",
        "description": "Runs one simulator algorithm for one route/day case.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Route date in YYYY-MM-DD format."},
                "ruta": {"type": "string", "description": "Route code."},
                "algo": {"type": "string", "description": "Algorithm id from list_algorithms."},
            },
            "required": ["date", "ruta", "algo"],
        },
    },
    {
        "name": "benchmark_algorithms",
        "description": "Benchmarks simulator algorithms over route/day cases.",
        "parameters": {
            "type": "object",
            "properties": {
                "algos": {"type": "array", "items": {"type": "string"}, "description": "Algorithm ids. Empty means all."},
                "max_cases": {"type": "integer", "description": "Maximum cases, default 10."},
                "seed": {"type": "integer", "description": "Random seed, default 42."},
                "min_clients": {"type": "integer", "description": "Minimum clients per case, default 5."},
            },
        },
    },
]


def _gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_CLOUD_API_KEY")


def _vertex_access_token() -> str | None:
    return os.getenv("VERTEX_AI_ACCESS_TOKEN") or os.getenv("GOOGLE_CLOUD_ACCESS_TOKEN")


def _gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value or "").strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, dt.date | dt.time | dt.datetime):
        return value.isoformat()
    return value


def _route_detail(dl: Any, date: str, ruta: str) -> dict[str, Any]:
    try:
        fecha = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError(f"Invalid date: {date}") from exc

    case = dl.build_case(fecha, ruta)
    clients_map = dl.all_clients()
    return {
        "date": str(case.date),
        "ruta": case.ruta,
        "repartidor": case.repartidor,
        "truck": {
            "code": case.truck.code,
            "name": case.truck.name,
            "pallet_capacity": case.truck.pallet_capacity,
            "max_weight_kg": case.truck.max_weight_kg,
        },
        "transports": list(case.raw_transports),
        "n_clients": case.n_clients,
        "total_volume_m3": case.total_volume_m3,
        "orders": [
            {
                "client_id": order.client_id,
                "client_name": clients_map.get(order.client_id).name if clients_map.get(order.client_id) else order.client_id,
                "visit_seq": order.visit_seq_actual,
                "expected_returnable_units": order.expected_returnable_units,
                "total_volume_m3": order.total_volume_m3,
                "total_weight_kg": order.total_weight_kg,
                "lines": [
                    {
                        "sku": line.sku,
                        "qty": line.qty,
                        "uma": line.uma,
                        "unit_volume_m3": line.unit_volume_m3,
                        "unit_weight_kg": line.unit_weight_kg,
                        "is_returnable": line.is_returnable,
                    }
                    for line in order.lines[:20]
                ],
            }
            for order in case.orders
        ],
    }


def _client_out(client: Any) -> dict[str, Any]:
    return {
        "client_id": client.client_id,
        "name": client.name,
        "address": client.address,
        "cp": client.cp,
        "city": client.city,
        "lat": client.lat,
        "lon": client.lon,
        "time_windows": [
            {
                "weekday": tw.weekday,
                "shift": tw.shift,
                "start": tw.start.isoformat(),
                "end": tw.end.isoformat(),
                "closed": tw.closed,
            }
            for tw in client.time_windows
        ],
    }


def _execute_tool(name: str, args: dict[str, Any], request: Request, frontend_context: dict[str, Any]) -> Any:
    dl = request.app.state.dl

    if name == "get_frontend_context":
        return frontend_context
    if name == "list_routes":
        df = dl.list_day_cases()
        return [
            {
                "fecha": str(row["fecha"]),
                "ruta": str(row["Ruta"]),
                "clients": int(row["clients"]),
                "lines": int(row["lines"]),
            }
            for _, row in df.head(100).iterrows()
        ]
    if name == "get_route_detail":
        return _route_detail(dl, str(args["date"]), str(args["ruta"]))
    if name == "list_clients":
        query = str(args.get("query") or "").lower()
        limit = max(1, min(100, int(args.get("limit") or 20)))
        clients = [_client_out(c) for c in dl.all_clients().values()]
        if query:
            clients = [
                c
                for c in clients
                if query in " ".join(str(c.get(k, "")) for k in ("client_id", "name", "address", "cp", "city")).lower()
            ]
        return clients[:limit]
    if name == "get_client":
        return _client_out(dl.get_client(str(args["client_id"])))
    if name == "list_algorithms":
        return {"algorithms": _list_algorithms()}
    if name == "list_days":
        return _list_days(min_clients=int(args.get("min_clients") or 5), head=int(args.get("head") or 20))
    if name == "run_simulation":
        return _run_one(str(args["date"]), str(args["ruta"]), str(args["algo"]))
    if name == "benchmark_algorithms":
        return _bench(
            algos=list(args.get("algos") or []),
            max_cases=max(1, min(50, int(args.get("max_cases") or 10))),
            seed=int(args.get("seed") or 42),
            min_clients=int(args.get("min_clients") or 5),
        )

    raise ValueError(f"Unknown tool: {name}")


def _gemini_generate(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = _gemini_api_key()
    vertex_token = _vertex_access_token()
    vertex_project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEX_AI_PROJECT")
    vertex_location = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("VERTEX_AI_LOCATION") or "global"

    headers = {"Content-Type": "application/json"}
    if vertex_project and vertex_token:
        service = "aiplatform.googleapis.com" if vertex_location == "global" else f"{vertex_location}-aiplatform.googleapis.com"
        model_path = f"projects/{vertex_project}/locations/{vertex_location}/publishers/google/models/{model}"
        url = f"https://{service}/v1/{model_path}:generateContent"
        headers["Authorization"] = f"Bearer {vertex_token}"
    elif api_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers["x-goog-api-key"] = api_key
    else:
        raise HTTPException(
            status_code=503,
            detail=(
                "Set GEMINI_API_KEY/GOOGLE_API_KEY, or set GOOGLE_CLOUD_PROJECT plus "
                "VERTEX_AI_ACCESS_TOKEN/GOOGLE_CLOUD_ACCESS_TOKEN, to enable copilot chat."
            ),
        )

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=_gemini_error_detail(detail)) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API unreachable: {exc.reason}") from exc


def _gemini_error_detail(raw_detail: str) -> str:
    try:
        body = json.loads(raw_detail)
        error = body.get("error") or {}
    except json.JSONDecodeError:
        return f"Gemini API error: {raw_detail}"

    status = error.get("status")
    message = error.get("message") or raw_detail
    reason = None
    for item in error.get("details") or []:
        if item.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
            reason = item.get("reason")
            break

    if reason == "API_KEY_SERVICE_BLOCKED":
        return (
            "Gemini API key is blocked from calling generativelanguage.googleapis.com. "
            "In Google Cloud, edit the API key restrictions and allow the Generative Language API, "
            "or remove API restrictions for local development."
        )
    if reason == "SERVICE_DISABLED":
        return (
            "Gemini API is disabled for this Google Cloud project. Enable the Generative Language API "
            "for the project attached to the API key, then restart the backend."
        )

    prefix = f"Gemini API error{f' ({status})' if status else ''}"
    return f"{prefix}: {message}"


def _response_parts(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = response.get("candidates") or []
    if not candidates:
        return []
    return candidates[0].get("content", {}).get("parts", []) or []


def _text_from_parts(parts: list[dict[str, Any]]) -> str:
    return "\n".join(part["text"] for part in parts if part.get("text")).strip()


def _history(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    contents = []
    for message in messages[-10:]:
        text = _strip_html(message.text)
        if not text:
            continue
        contents.append(
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": text[:4000]}],
            }
        )
    return contents


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")

    model = _gemini_model()
    contents = _history(body.messages)
    context_json = json.dumps(_json_safe(body.frontend_context), ensure_ascii=False)[:12000]
    contents.append(
        {
            "role": "user",
            "parts": [
                {
                    "text": f"Current frontend context JSON:\n{context_json}\n\nUser asks:\n{message}",
                }
            ],
        }
    )

    config = {
        "temperature": 0.35,
        "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],
    }
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": config["temperature"]},
        "tools": config["tools"],
    }
    tool_calls: list[dict[str, Any]] = []

    for _ in range(5):
        response = _gemini_generate(model, payload)
        parts = _response_parts(response)
        calls = [part["functionCall"] for part in parts if part.get("functionCall")]
        if not calls:
            reply = _text_from_parts(parts)
            return ChatResponse(reply=reply or "I could not produce a response.", tool_calls=tool_calls, model=model)

        contents.append(response["candidates"][0]["content"])
        response_parts = []
        for call in calls:
            name = call.get("name")
            args = call.get("args") or {}
            try:
                result = _json_safe(_execute_tool(name, args, request, body.frontend_context))
                tool_calls.append({"name": name, "args": args, "ok": True})
            except Exception as exc:
                result = {"error": str(exc)}
                tool_calls.append({"name": name, "args": args, "ok": False, "error": str(exc)})

            function_response = {
                "name": name,
                "response": {"result": result},
            }
            if call.get("id"):
                function_response["id"] = call["id"]
            response_parts.append({"functionResponse": function_response})

        contents.append({"role": "user", "parts": response_parts})
        payload["contents"] = contents

    return ChatResponse(
        reply="I reached the tool-call limit before a final answer. Try narrowing the question.",
        tool_calls=tool_calls,
        model=model,
    )
