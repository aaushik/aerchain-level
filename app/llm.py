"""One small seam between the app and the model provider.

Two operations are all the product needs:
  read_json(system, parts)            -> dict        (extraction; multimodal in, JSON out)
  run_tools(system, history, tools)   -> text        (co-pilot and analyst; model-driven tool loop)

History is provider-neutral: [{"role": "user"|"assistant", "text": str}]. The tool calls made inside a
turn are replayed to the model as a short text trace, so a conversation survives a provider switch.
Switch with LLM_PROVIDER=gemini|claude in .env.
"""
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
import logging

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger("uvicorn.error")

PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK = [m.strip() for m in os.environ.get("GEMINI_FALLBACK", "gemini-2.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
_T = threading.local()      # which model actually answered, per request thread (extractions run in parallel)


def primary():
    """The configured model. Anything else that answers is a fallback, and the UI says so."""
    return f"{PROVIDER}:{CLAUDE_MODEL if PROVIDER == 'claude' else GEMINI_MODEL}"


def _answered(model):
    if PROVIDER == "claude" and model.startswith(CLAUDE_MODEL + "-"):    # dated snapshot of the configured alias
        model = CLAUDE_MODEL
    name = f"{PROVIDER}:{model}"
    _T.last = name
    if name not in getattr(_T, "used", []):
        _T.used = getattr(_T, "used", []) + [name]


def begin_turn():
    _T.used, _T.last = [], None


def used_models():
    """Every model that answered since begin_turn() in this thread, in order."""
    return list(getattr(_T, "used", []))


def model_name():
    """The model that answered the most recent call in this thread, else the configured one."""
    return getattr(_T, "last", None) or primary()


def text_part(text):
    return {"type": "text", "text": text}


def file_part(name, mime, data: bytes):
    return {"type": "file", "name": name, "mime": mime, "data": data}


def parse_json(text):
    """Models wrap JSON in fences or prose often enough that we never trust the raw string."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Model did not return a JSON object")
    return json.loads(t[start:end + 1])


# ------------------------------------------------------------------ Gemini
GEMINI_TIMEOUT_S = int(os.environ.get("GEMINI_TIMEOUT_S", "90"))   # a stalled model hands over to the next one


def _gemini():
    from google import genai
    from google.genai import types
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"],
                        http_options=types.HttpOptions(api_version="v1beta", timeout=GEMINI_TIMEOUT_S * 1000))


_COOLDOWN = {}          # model -> time until which we skip it (it just told us it is overloaded or out of quota)


def _gemini_call(client, **kw):
    """Walk the model chain. A model that returns overload/quota is rested for a while, so one bad model
    does not tax every step of a tool loop. Which model actually answered is recorded and shown in the UI."""
    from google.genai import errors
    chain = list(dict.fromkeys([kw.pop("model"), *GEMINI_FALLBACK]))
    last = None
    for round_ in range(2):
        for model in chain:
            until = _COOLDOWN.get(model, 0)
            if until == float("inf") or (until > time.time() and round_ == 0):
                continue
            t0 = time.time()
            try:
                resp = client.models.generate_content(model=model, **kw)
                _answered(model)
                log.info("gemini %s ok in %.1fs", model, time.time() - t0)
                return resp
            except errors.APIError as e:
                last = e
                code = getattr(e, "code", None)
                log.warning("gemini %s failed %s in %.1fs", model, code, time.time() - t0)
                if code == 404:                              # model retired or not on this key: never try it again
                    _COOLDOWN[model] = float("inf")
                    continue
                if code not in (402, 429, 500, 502, 503, 504):
                    raise
                _COOLDOWN[model] = time.time() + (600 if code in (402, 429) else 180)
            except Exception as e:                           # timeouts and dropped connections
                if "timeout" not in type(e).__name__.lower() and "timed out" not in str(e).lower():
                    raise
                last = e
                log.warning("gemini %s timed out after %.1fs", model, time.time() - t0)
                _COOLDOWN[model] = time.time() + 180
        time.sleep(5)
    raise last or RuntimeError("No Gemini model available")


def _gemini_parts(parts):
    from google.genai import types
    out = []
    for p in parts:
        if p["type"] == "text":
            out.append(types.Part.from_text(text=p["text"]))
        else:
            out.append(types.Part.from_text(text=f"[Attached file: {p['name']}]"))
            out.append(types.Part.from_bytes(data=p["data"], mime_type=p["mime"]))
    return out


def _gemini_json(system, parts, max_tokens):
    from google.genai import types
    resp = _gemini_call(
        _gemini(), model=GEMINI_MODEL,
        contents=[types.Content(role="user", parts=_gemini_parts(parts))],
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           temperature=0, max_output_tokens=max_tokens))
    return parse_json(resp.text or "")


def _gemini_tools(system, history, tools, execute, max_steps):
    from google.genai import types
    client = _gemini()
    decl = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["schema"]) for t in tools]
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=0, tools=[types.Tool(function_declarations=decl)],
                                      automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contents = [types.Content(role="user" if h["role"] == "user" else "model",
                              parts=[types.Part.from_text(text=h["text"])]) for h in history]
    nudged = False
    for _ in range(max_steps):
        resp = _gemini_call(client, model=GEMINI_MODEL, contents=contents, config=cfg)
        calls = resp.function_calls or []
        if not calls:
            if (resp.text or "").strip() or nudged:
                return resp.text or "The model returned no answer. Please ask again."
            nudged = True                                 # some models stop silently after their last tool call
            if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                contents.append(resp.candidates[0].content)
            contents.append(types.Content(role="user", parts=[types.Part.from_text(
                text="Now write your answer for the buyer, using only the tool results above.")]))
            continue
        contents.append(resp.candidates[0].content)      # keeps the model's own call (and thought signatures) intact
        results = []
        for c in calls:
            results.append(types.Part.from_function_response(name=c.name, response={"result": execute(c.name, dict(c.args or {}))}))
        contents.append(types.Content(role="user", parts=results))
    return "I ran out of steps before finishing. Try a narrower question."


# ------------------------------------------------------------------ Claude
def _claude():
    import anthropic
    return anthropic.Anthropic()

# Server-side refusal fallback is on: if the primary model declines, the API re-runs on a fallback model.
_CLAUDE_KW = dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")


def _claude_parts(parts):
    out = []
    for p in parts:
        if p["type"] == "text":
            out.append({"type": "text", "text": p["text"]})
            continue
        b64 = base64.standard_b64encode(p["data"]).decode()
        out.append({"type": "text", "text": f"[Attached file: {p['name']}]"})
        kind = "document" if p["mime"] == "application/pdf" else "image"
        out.append({"type": kind, "source": {"type": "base64", "media_type": p["mime"], "data": b64}})
    return out


def _claude_json(system, parts, max_tokens):
    with _claude().beta.messages.stream(model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": _claude_parts(parts)}], **_CLAUDE_KW) as s:
        msg = s.get_final_message()
    _answered(getattr(msg, "model", None) or CLAUDE_MODEL)
    if msg.stop_reason == "refusal":
        raise RuntimeError("The model declined this request.")
    return parse_json("".join(b.text for b in msg.content if b.type == "text"))


def _claude_tools(system, history, tools, execute, max_steps):
    client = _claude()
    tdefs = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]
    messages = [{"role": h["role"], "content": h["text"]} for h in history]
    for _ in range(max_steps):
        resp = client.beta.messages.create(model=CLAUDE_MODEL, max_tokens=16000, system=system, tools=tdefs,
                                           messages=messages, **_CLAUDE_KW)
        _answered(getattr(resp, "model", None) or CLAUDE_MODEL)
        if resp.stop_reason == "refusal":
            return "The model declined this request."
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(execute(b.name, dict(b.input)), default=str)})
        messages.append({"role": "user", "content": results})
    return "I ran out of steps before finishing. Try a narrower question."


# ------------------------------------------------------------------ public
def read_json(system, parts, max_tokens=32000):
    return (_claude_json if PROVIDER == "claude" else _gemini_json)(system, parts, max_tokens)


def run_tools(system, history, tools, execute, max_steps=12):
    def safe(name, args):
        try:
            return execute(name, args)
        except Exception as e:                      # the model gets the error and can correct its call
            return {"error": f"{type(e).__name__}: {e}"}
    return (_claude_tools if PROVIDER == "claude" else _gemini_tools)(system, history, tools, safe, max_steps)
