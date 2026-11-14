# Model Arena

**Live:** https://arena.satinlabs.dev · **Mobile app:** [arena-mobile](https://github.com/satellitelateh-bot/arena-mobile)

Ask one question, watch several AI models answer side by side in real time, then vote for the best answer. Model names stay hidden until you vote, so the vote isn't biased by brand.

## Features

- **Parallel streaming.** Every model streams its answer token by token at the same time (Server-Sent Events).
- **Blind voting.** Answers show as Model A / B / C in shuffled order. Names are revealed after the vote.
- **Leaderboard by language.** Win rates are tracked separately for Thai and English prompts, because a model that is strong in English is not always strong in Thai.
- **Live metrics.** Time to first token, tokens per second, token count, and estimated cost in THB for each answer.
- **Cloud vs self-hosted.** Hosted models on Groq compete with a small Llama model running on my own server, so you can see the speed, cost, and privacy trade-off with real numbers.

## Architecture

```
Browser / iPhone / Android
          │  HTTPS
          ▼
   Caddy (auto HTTPS)
          │
          ▼
  FastAPI  (/api/run → SSE stream, /api/vote, /api/leaderboard)
     │                 │
     ▼                 ▼
 Groq API        Ollama (Llama 3.2, same server)
     │
  SQLite (votes, Docker volume)
```

The web app and the mobile app use the same API and the same database.

## Engineering notes

- **Security:** rate limits per IP (runs and votes), prompt length limits, input validation, parameterized SQL, and `textContent` rendering to block XSS. API keys live only in a server-side `.env` file.
- **Deployment:** Docker Compose on a DigitalOcean VPS. A push to `main` triggers GitHub Actions, which deploys over SSH with a dedicated deploy key and then runs a health check.
- **Resource-aware:** the server has 4 GB RAM, so it reuses one shared Ollama container (the same one used by my LINE receptionist project) instead of running a second one.

## Tech stack

Python · FastAPI · SSE · SQLite · Groq · Ollama · Docker · Caddy · GitHub Actions · vanilla JS

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key" > .env
uvicorn server:app --reload --port 8001
```

---
Built by **Satin Labs** · [satinlabs.dev](https://satinlabs.dev)
