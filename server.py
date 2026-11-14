import json
import os
import sqlite3
import time
from collections import defaultdict, deque

import ollama
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from groq import Groq
from pydantic import BaseModel

load_dotenv()
groq = Groq()
app = FastAPI(title="Satin Labs Model Arena")

MODELS = {
    "gpt-oss-120b": {"provider": "groq", "id": "openai/gpt-oss-120b", "label": "gpt-oss 120B", "usd_per_m": 0.60},
    "gpt-oss-20b": {"provider": "groq", "id": "openai/gpt-oss-20b", "label": "gpt-oss 20B", "usd_per_m": 0.30},
    "qwen-27b": {"provider": "groq", "id": "qwen/qwen3.8-27b", "label": "Qwen 27B", "usd_per_m": 0.40},
    "llama-3b-local": {"provider": "ollama", "id": os.environ.get("LOCAL_MODEL", "llama3.2"), "label": "Llama (my server)", "usd_per_m": 0.0},
}
USD_TO_THB = 35
MAX_PROMPT = 2000
MAX_RUNS_PER_HOUR = 20
MAX_VOTES_PER_HOUR = 30
DB = os.environ.get("DB_PATH", "arena.db")
RECENT = defaultdict(deque)


def connect():
    db = sqlite3.connect(DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created REAL NOT NULL, lang TEXT NOT NULL, winner TEXT NOT NULL, models TEXT NOT NULL)"
    )
    return db


class RunIn(BaseModel):
    model: str
    prompt: str
    system: str = ""
    temperature: float = 0.7


class VoteIn(BaseModel):
    winner: str
    models: list[str]
    prompt: str


def client_ip(request):
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host


def allow(key, limit):
    now = time.time()
    hits = RECENT[key]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def lang_of(text):
    thai = sum("฀" <= ch <= "๿" for ch in text)
    return "th" if thai > 0.2 * max(len(text), 1) else "en"


def event(kind, **data):
    return f"data: {json.dumps({'type': kind, **data}, ensure_ascii=False)}\n\n"


def stream_groq(m, messages, temperature):
    tokens = None
    for chunk in groq.chat.completions.create(
        model=m["id"], messages=messages, temperature=temperature, stream=True
    ):
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content, None
        extra = getattr(chunk, "x_groq", None)
        usage = getattr(extra, "usage", None) if extra else None
        if usage:
            tokens = usage.completion_tokens
    yield "", tokens


def stream_ollama(m, messages, temperature):
    for chunk in ollama.chat(
        model=m["id"], messages=messages, stream=True, options={"temperature": temperature}
    ):
        if chunk["message"]["content"]:
            yield chunk["message"]["content"], None
        if chunk.get("done"):
            yield "", chunk.get("eval_count")


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/models")
def models():
    return [{"key": k, "label": v["label"], "usd_per_m": v["usd_per_m"]} for k, v in MODELS.items()]


@app.post("/api/run")
def run(body: RunIn, request: Request):
    m = MODELS.get(body.model)
    if not m:
        raise HTTPException(400, "Unknown model")
    prompt = body.prompt.strip()[:MAX_PROMPT]
    if not prompt:
        raise HTTPException(400, "Empty prompt")
    if not allow(client_ip(request) + "|run|" + body.model, MAX_RUNS_PER_HOUR):
        raise HTTPException(429, "Too many runs. Please try again later.")

    messages = []
    if body.system.strip():
        messages.append({"role": "system", "content": body.system.strip()[:1000]})
    messages.append({"role": "user", "content": prompt})
    temperature = max(0.0, min(body.temperature, 1.5))
    source = stream_groq if m["provider"] == "groq" else stream_ollama

    def generate():
        start = time.time()
        first = None
        chars = 0
        tokens = None
        try:
            for text, count in source(m, messages, temperature):
                if text:
                    first = first or time.time()
                    chars += len(text)
                    yield event("token", text=text)
                if count:
                    tokens = count
            end = time.time()
            tokens = tokens or max(1, chars // 3)
            gen_time = max(end - (first or end), 0.001)
            yield event(
                "done",
                first_token_s=round((first or end) - start, 2),
                total_s=round(end - start, 2),
                tokens=tokens,
                tokens_per_s=round(tokens / max(end - start, 0.001)),
                cost_thb=round(tokens / 1_000_000 * m["usd_per_m"] * USD_TO_THB, 4),
            )
        except Exception as error:
            yield event("error", message=str(error)[:200])

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/vote")
def vote(body: VoteIn, request: Request):
    keys = list(dict.fromkeys(body.models))
    if len(keys) < 2 or any(k not in MODELS for k in keys) or body.winner not in keys:
        raise HTTPException(400, "Invalid vote")
    if not allow(client_ip(request) + "|vote", MAX_VOTES_PER_HOUR):
        raise HTTPException(429, "Too many votes. Please try again later.")
    with connect() as db:
        db.execute(
            "INSERT INTO votes (created, lang, winner, models) VALUES (?, ?, ?, ?)",
            (time.time(), lang_of(body.prompt), body.winner, ",".join(keys)),
        )
    return {"labels": {k: MODELS[k]["label"] for k in keys}}


@app.get("/api/leaderboard")
def leaderboard():
    with connect() as db:
        rows = db.execute("SELECT lang, winner, models FROM votes").fetchall()
    stats = {lang: {k: {"wins": 0, "games": 0} for k in MODELS} for lang in ("th", "en")}
    for lang, winner, models_csv in rows:
        for k in models_csv.split(","):
            if k in stats[lang]:
                stats[lang][k]["games"] += 1
        if winner in stats[lang]:
            stats[lang][winner]["wins"] += 1
    result = {}
    for lang, table in stats.items():
        items = [
            {"label": MODELS[k]["label"], "wins": s["wins"], "games": s["games"],
             "win_rate": round(100 * s["wins"] / s["games"]) if s["games"] else 0}
            for k, s in table.items()
        ]
        items.sort(key=lambda x: (x["win_rate"], x["games"]), reverse=True)
        result[lang] = items
    result["total_votes"] = len(rows)
    return result
