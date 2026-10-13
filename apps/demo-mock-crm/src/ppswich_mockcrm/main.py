"""Synthetic mock CRM for the pp swich demo.

Entirely fictional data. Two dataset scopes model two different account
authorizations:

- keys like ``MOCKKEY-DEMO-A-...`` -> the "Demo A" dataset
- keys like ``MOCKKEY-DEMO-B-...`` -> the "Demo B" (QA) dataset

The API deliberately returns leaky fields (``access_token``,
``internal_note``, ``account_email``) and a ``Set-Cookie`` header so the
pp swich response guard has something realistic to strip (SEC-003/SEC-020).
No real credential can ever be valid here: only ``MOCKKEY-*`` values are
accepted.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="pp swich demo mock CRM", version="0.1.0")


class RefundRequest(BaseModel):
    amount: float = Field(ge=0, le=1_000_000)

_DATASETS: dict[str, dict[str, dict]] = {
    "A": {
        "C-1024": {"id": "C-1024", "display_name": "Acme Industries", "tier": "gold"},
        "C-2048": {"id": "C-2048", "display_name": "Globex Corporation", "tier": "silver"},
        "C-4096": {"id": "C-4096", "display_name": "Initech", "tier": "bronze"},
    },
    "B": {
        "C-1024": {"id": "C-1024", "display_name": "Acme Industries (QA copy)", "tier": "gold"},
        "C-2048": {"id": "C-2048", "display_name": "Globex Corporation (QA copy)", "tier": "silver"},  # noqa: E501
        "C-8192": {"id": "C-8192", "display_name": "Umbrella Demo (QA)", "tier": "bronze"},
    },
}

_SLOW_PREFIX = "SLOW-"


def _dataset_for_key(api_key: str) -> str:
    if not api_key.startswith("MOCKKEY-DEMO-"):
        raise HTTPException(status_code=401, detail="invalid_api_key")
    scope = api_key[len("MOCKKEY-DEMO-") :].split("-", 1)[0]
    if scope not in _DATASETS:
        raise HTTPException(status_code=401, detail="invalid_api_key")
    return scope


def _leaky(customer: dict) -> dict:
    body = dict(customer)
    # deliberately leaky fields: the response guard must strip these
    body["access_token"] = "CANARY_TOKEN_NOT_REAL"  # noqa: S105 - synthetic canary
    body["account_email"] = "ops-not-real@example.invalid"
    body["internal_note"] = "internal-only note with no real data"
    return body


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "mock-crm"}


@app.get("/")
def root() -> dict:
    return {"service": "pp swich demo mock CRM", "docs": "/docs"}


@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: str, x_api_key: str = Header(default="")) -> JSONResponse:
    dataset = _dataset_for_key(x_api_key)
    if customer_id.startswith(_SLOW_PREFIX):
        # deterministic slow path for the in-flight profile snapshot test
        time.sleep(0.4)
        customer_id = customer_id[len(_SLOW_PREFIX) :]
    record = _DATASETS[dataset].get(customer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="customer_not_found")
    response = JSONResponse(_leaky(record))
    response.headers["Set-Cookie"] = "MOCK_SESSION=not-real; Path=/; HttpOnly"
    return response


@app.post("/api/customers/{customer_id}/refund")
def refund_customer(
    customer_id: str, payload: RefundRequest, x_api_key: str = Header(default="")
) -> dict:
    dataset = _dataset_for_key(x_api_key)
    if customer_id not in _DATASETS[dataset]:
        raise HTTPException(status_code=404, detail="customer_not_found")
    return {
        "refund_id": f"R-{dataset}-{customer_id}",
        "status": "issued",
        "amount": payload.amount,
    }


@app.get("/api/overflow")
def overflow() -> dict:
    """~2 MB payload to exercise response size limits (SEC-020)."""
    return {"filler": "x" * (2 * 1024 * 1024)}


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    port = int(os.environ.get("PP_SWICH_MOCKCRM_PORT", "8913"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
