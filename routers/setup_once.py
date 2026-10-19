"""ONE-TIME setup: create Gaurav's Supabase Auth user and link to public.users.
DELETE THIS FILE after use. Protected by a setup token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client

from deps import get_supabase

router = APIRouter(prefix="/api/_setup", tags=["setup"], include_in_schema=False)

SETUP_TOKEN = "jup-setup-9f3k2m8x"
ADMIN_EMAIL = "patel90g@gmail.com"
ADMIN_PASSWORD = "Z16JSIZDRhU9btGxn9A9_g"  # Gaurav changes this on first login
ADMIN_NAME = "Gaurav Patel"


class SetupIn(BaseModel):
    token: str


@router.post("/create-admin")
def create_admin(body: SetupIn, supabase: Client = Depends(get_supabase)):
    if body.token != SETUP_TOKEN:
        raise HTTPException(404, "Not found")
    # Create auth user (idempotent-ish: catch if exists)
    try:
        created = supabase.auth.admin.create_user({
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "email_confirm": True,
            "user_metadata": {"full_name": ADMIN_NAME},
        })
        auth_id = created.user.id
    except Exception as exc:
        # Maybe already exists — look it up
        users = supabase.auth.admin.list_users()
        match = [u for u in users if u.email.lower() == ADMIN_EMAIL.lower()]
        if not match:
            raise HTTPException(500, f"Could not create or find user: {exc}")
        auth_id = match[0].id
    # Link to public.users
    rows = supabase.table("users").select("id").eq("email", ADMIN_EMAIL).execute().data or []
    if rows:
        supabase.table("users").update(
            {"auth_user_id": auth_id, "role": "master_admin",
             "full_name": ADMIN_NAME, "is_active": True}).eq("id", rows[0]["id"]).execute()
    else:
        supabase.table("users").insert(
            {"auth_user_id": auth_id, "email": ADMIN_EMAIL,
             "full_name": ADMIN_NAME, "role": "master_admin",
             "is_active": True}).execute()
    return {"ok": True, "email": ADMIN_EMAIL}
