"""
Jupiter Supply Ltd. — shared dependencies.

- Supabase client (service_role, server-side only — bypasses RLS by design)
- NVIDIA NIM client settings
- YES-gate enforcement: any endpoint that SENDS something externally
  (email, quote, invoice) must call require_approval() first.
  Without an approved approvals row -> HTTP 403. No exceptions.
"""
from __future__ import annotations

import os
from functools import lru_cache

import httpx
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from pydantic_settings import BaseSettings
from supabase import create_client, Client

load_dotenv()


class Settings(BaseSettings):
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    nvidia_base_url: str = os.getenv(
        "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
    )
    nvidia_vision_model: str = os.getenv(
        "NVIDIA_VISION_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b"
    )
    search_api_key: str = os.getenv("SEARCH_API_KEY", "")
    # Serper.dev (free tier, no card). Override via env if switching providers.
    search_api_url: str = os.getenv(
        "SEARCH_API_URL", "https://google.serper.dev/search"
    )
    cors_origins: str = os.getenv("CORS_ORIGINS", "https://jupitersupply.ca")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_supabase(settings: Settings = Depends(get_settings)) -> Client:
    """Service-role client. Runs server-side only; never expose the key."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured"
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def nvidia_headers(settings: Settings) -> dict[str, str]:
    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# YES-gate — the single choke point for every outbound send.
# ---------------------------------------------------------------------------
async def require_approval(
    entity_type: str,
    entity_id: str,
    supabase: Client = Depends(get_supabase),
) -> dict:
    """
    Check the approvals table for an APPROVED row for this entity.
    Returns the approval row. Raises 403 when no approval exists.

    Usage (in any send endpoint):
        approval = await require_approval("quote", quote_id, supabase)
    """
    res = (
        supabase.table("approvals")
        .select("id,status,decided_by,decided_at")
        .eq("entity_type", entity_type)
        .eq("entity_id", entity_id)
        .eq("status", "approved")
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"No approved approval for {entity_type} {entity_id}. "
                "Gaurav (Master Admin) must approve before anything is sent."
            ),
        )
    return rows[0]


async def log_activity(
    supabase: Client,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Append-only audit trail. Best-effort: never breaks the caller."""
    try:
        supabase.table("activity_log").insert(
            {
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "details": details or {},
            }
        ).execute()
    except Exception:
        pass  # audit logging must never fail a request


# ---------------------------------------------------------------------------
# Public-safe product serialization.
# The products_public VIEW already excludes supplier_id / cost_price, and
# masks sell_price unless show_price is true. This serializer is a second
# layer of defense: even if someone queries the raw table by mistake,
# internal fields can never leave through these endpoints.
# ---------------------------------------------------------------------------
PUBLIC_PRODUCT_FIELDS = {
    "id",
    "sku",
    "name",
    "description",
    "category_id",
    "unit",
    "sell_price",   # already NULL when show_price=false (view masks it)
    "show_price",
    "is_active",
}


def public_product(row: dict) -> dict:
    """Strip anything that is not an explicitly public field."""
    return {k: v for k, v in row.items() if k in PUBLIC_PRODUCT_FIELDS}


# Shared HTTP client for NVIDIA / search-provider calls (keep-alive).
_http_client: httpx.AsyncClient | None = None


async def get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=90.0)
    return _http_client
