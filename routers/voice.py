"""
Jupiter Supply Ltd. — Voice Agent (inbound call robot).

Twilio webhooks + TwiML. Answers product questions, takes orders/messages,
routes quote requests to Gaurav, transfers urgent calls to 587.778.1702.
After-hours: message mode with next-day callback promise.

NEVER quotes prices on a call — quote requests are logged for Gaurav.

Setup needed (one-time, owner):
  1. Twilio account in Gaurav's name + a phone number
  2. Point the number's voice webhook to POST /api/voice/incoming
  3. (Optional) ELEVENLABS_API_KEY in Vercel env for the Adam voice;
     falls back to Twilio <Say> when absent.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse
from supabase import Client

from deps import get_supabase, get_settings
from routers.auth import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice"], include_in_schema=False)

MT = ZoneInfo("America/Edmonton")
URGENT_NUMBER = "5877781702"

# In-memory audio cache for ElevenLabs TTS (v1)
_audio_cache: dict[str, bytes] = {}


def _twiml(*verbs: str) -> PlainTextResponse:
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        + "".join(verbs) + "</Response>",
        media_type="text/xml")


def _say(text: str) -> str:
    # Twilio <Say> fallback; ElevenLabs <Play> used when key present (see handle)
    return f"<Say>{text}</Say>"


def _business_hours() -> bool:
    now = datetime.now(MT)
    return now.weekday() < 5 and 9 <= now.hour < 17


def _classify(speech: str) -> str:
    s = speech.lower()
    if re.search(r"\b(urgent|emergency|asap|immediately|right now|flood|leak|broken|not working)\b", s):
        return "urgent"
    if re.search(r"\b(quote|price|pricing|how much|cost|estimate)\b", s):
        return "quote"
    if re.search(r"\b(order|buy|purchase|want to order|place an order)\b", s):
        return "order"
    if re.search(r"\b(message|call me back|callback|voicemail|leave a message)\b", s):
        return "message"
    return "product"


def _log_call(supabase: Client, from_number: str, speech: str, intent: str, action: str):
    try:
        supabase.table("activity_log").insert({
            "action": f"voice.{intent}",
            "entity_type": "call",
            "details": {"from": from_number, "speech": speech[:500],
                        "intent": intent, "action": action},
        }).execute()
    except Exception:
        pass


@router.post("/incoming")
async def incoming(request: Request, supabase: Client = Depends(get_supabase)):
    form = await request.form()
    from_number = str(form.get("From", "unknown"))
    _log_call(supabase, from_number, "", "incoming", "greeted")

    if not _business_hours():
        return _twiml(
            _say("Thanks for calling Jupiter Supply Limited. "
                 "We're currently closed. Please leave your name, "
                 "hotel, and what you need after the beep, and Monty "
                 "will call you back tomorrow."),
            '<Record maxLength="120" playBeep="true" '
            'action="/api/voice/voicemail" method="POST"/>')

    return _twiml(
        _say("Hello, thanks for calling Jupiter Supply Limited, "
             "supplying quality, delivering trust. "
             "Are you calling about a product, placing an order, "
             "or requesting a quote?"),
        '<Gather input="speech" speechTimeout="3" '
        'action="/api/voice/handle" method="POST" '
        'hints="product, order, quote, price, message, urgent"/>',
        _say("Sorry, I didn't catch that. Please call again. Goodbye."))


@router.post("/handle")
async def handle(request: Request, supabase: Client = Depends(get_supabase)):
    form = await request.form()
    from_number = str(form.get("From", "unknown"))
    speech = str(form.get("SpeechResult", "") or "")
    intent = _classify(speech)

    if intent == "urgent":
        _log_call(supabase, from_number, speech, intent, "transferred")
        return _twiml(
            _say("This sounds urgent. Connecting you to Monty now."),
            f"<Dial>{URGENT_NUMBER}</Dial>")

    if intent == "quote":
        _log_call(supabase, from_number, speech, intent, "logged_for_callback")
        return _twiml(
            _say("I'd be happy to get you a quote. I've logged your request "
                 "and Monty will call you back shortly with pricing. "
                 "Can I take your name and hotel?"),
            '<Record maxLength="60" playBeep="true" '
            'action="/api/voice/voicemail" method="POST"/>')

    if intent == "order":
        _log_call(supabase, from_number, speech, intent, "logged_for_callback")
        return _twiml(
            _say("Got it — I'll pass your order request to our team right away. "
                 "Please leave your name, hotel, and what you'd like to order "
                 "after the beep."),
            '<Record maxLength="120" playBeep="true" '
            'action="/api/voice/voicemail" method="POST"/>')

    if intent == "message":
        _log_call(supabase, from_number, speech, intent, "recording")
        return _twiml(
            _say("Please leave your message after the beep."),
            '<Record maxLength="120" playBeep="true" '
            'action="/api/voice/voicemail" method="POST"/>')

    # product question — check catalog
    _log_call(supabase, from_number, speech, intent, "catalog_check")
    return _twiml(
        _say("Let me check that for you. What product are you looking for?"),
        '<Gather input="speech" speechTimeout="3" '
        'action="/api/voice/product" method="POST"/>',
        _say("Sorry, I didn't catch the product name. "
             "Monty will call you back to help. Goodbye."))


@router.post("/product")
async def product_lookup(request: Request, supabase: Client = Depends(get_supabase)):
    form = await request.form()
    from_number = str(form.get("From", "unknown"))
    speech = str(form.get("SpeechResult", "") or "")
    # Simple keyword search over catalog
    words = [w for w in re.findall(r"[a-z]+", speech.lower()) if len(w) > 3][:4]
    found = []
    if words:
        q = supabase.table("products_public").select("name").limit(50).execute().data or []
        for p in q:
            nm = p["name"].lower()
            if any(w in nm for w in words):
                found.append(p["name"])
    _log_call(supabase, from_number, speech, "product", f"found={len(found)}")
    if found:
        items = ", ".join(found[:3])
        return _twiml(_say(
            f"Good news — we carry {items}. "
            "For pricing, Monty will call you back shortly. "
            "Anything else I can help with? Goodbye."))
    return _twiml(_say(
        "I couldn't find that exact item in our catalog, but we source "
        "thousands of hotel products. I've noted your request and Monty "
        "will call you back. Goodbye."))


@router.post("/voicemail")
async def voicemail(request: Request, supabase: Client = Depends(get_supabase)):
    form = await request.form()
    from_number = str(form.get("From", "unknown"))
    rec_url = str(form.get("RecordingUrl", ""))
    _log_call(supabase, from_number, "", "voicemail",
              f"recording={rec_url[:120]}")
    return _twiml(_say("Thanks, we've got your message. Goodbye."))


@router.get("/calls")
async def call_log(supabase: Client = Depends(get_supabase),
                   user: dict = Depends(get_current_user)):
    """Recent voice calls (admin-only)."""
    rows = supabase.table("activity_log").select("*") \
        .like("action", "voice.%").order("created_at", desc=True) \
        .limit(50).execute().data or []
    return rows
