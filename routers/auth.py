"""
Jupiter Supply Ltd. — Auth API.

Email + password login via Supabase Auth. Returns a JWT that the
dashboard sends as `Authorization: Bearer <token>` on every /api/* call.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client

from deps import get_supabase

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class LoginIn(BaseModel):
    email: str
    password: str


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    supabase: Client = Depends(get_supabase),
) -> dict:
    """Validate the Supabase JWT and return the linked public.users row."""
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Login required")
    try:
        auth_user = supabase.auth.get_user(creds.credentials)
        uid = auth_user.user.id
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Session expired — please log in again")
    rows = supabase.table("users").select("*").eq("auth_user_id", uid).execute().data or []
    if not rows or not rows[0].get("is_active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account not authorized")
    return rows[0]


@router.post("/login")
def login(body: LoginIn, supabase: Client = Depends(get_supabase)):
    try:
        res = supabase.auth.sign_in_with_password({
            "email": body.email.strip().lower(),
            "password": body.password,
        })
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Wrong email or password")
    if not res.session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Wrong email or password")
    me = supabase.table("users").select("id,email,full_name,role").eq(
        "auth_user_id", res.user.id).execute().data
    if not me:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account not authorized")
    return {"access_token": res.session.access_token,
            "expires_in": res.session.expires_in,
            "user": me[0]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"],
            "full_name": user.get("full_name"), "role": user["role"]}


class PasswordIn(BaseModel):
    new_password: str


@router.post("/change-password")
def change_password(body: PasswordIn,
                    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                    supabase: Client = Depends(get_supabase)):
    if not creds or not creds.credentials:
        raise HTTPException(401, "Login required")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        # Validate the token, then update that user's password via admin API
        auth_user = supabase.auth.get_user(creds.credentials)
        supabase.auth.admin.update_user_by_id(
            auth_user.user.id, {"password": body.new_password})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, f"Could not change password: {exc}")
    return {"ok": True}
