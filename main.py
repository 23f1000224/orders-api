
import base64
import math
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request, Header, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

TOTAL_ORDERS = 59
RATE_LIMIT = 16
WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

idempotency_store = {}
lock = Lock()
client_requests = defaultdict(deque)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = time.monotonic()

    with lock:
        requests = client_requests[client_id]

        while requests and now - requests[0] >= WINDOW_SECONDS:
            requests.popleft()

        if len(requests) >= RATE_LIMIT:
            retry_after = max(
                1,
                math.ceil(WINDOW_SECONDS - (now - requests[0]))
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        requests.append(now)

    return await call_next(request)


@app.get("/")
def home():
    return {
        "message": "Orders API is running",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
    }


@app.post("/orders", status_code=201)
def create_order(
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    payload: dict = Body(default={}),
):
    with lock:
        if idempotency_key in idempotency_store:
            return JSONResponse(
                status_code=200,
                content=idempotency_store[idempotency_key],
            )

        order = {
            "id": str(uuid.uuid4()),
            "details": payload,
        }

        idempotency_store[idempotency_key] = order

        return JSONResponse(status_code=201, content=order)


@app.get("/orders")
def list_orders(limit: int = 10, cursor: str | None = None):
    if limit < 1:
        return JSONResponse(
            status_code=400,
            content={"detail": "limit must be at least 1"},
        )

    if cursor is None:
        start = 0
    else:
        try:
            start = int(
                base64.urlsafe_b64decode(
                    cursor + "=" * (-len(cursor) % 4)
                ).decode()
            )
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid cursor"},
            )

    if start < 0 or start > TOTAL_ORDERS:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid cursor"},
        )

    end = min(start + limit, TOTAL_ORDERS)

    items = [
        {
            "id": order_id,
            "details": {"name": f"Order {order_id}"},
        }
        for order_id in range(start + 1, end + 1)
    ]

    next_cursor = None
    if end < TOTAL_ORDERS:
        next_cursor = base64.urlsafe_b64encode(
            str(end).encode()
        ).decode().rstrip("=")

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
