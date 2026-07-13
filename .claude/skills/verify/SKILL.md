---
name: verify
description: Cold-start recipe for running Private RAG Apps end-to-end (backend + frontend) to observe a change, not just test/build it.
---

# Verify: Private RAG Apps

No `make web` target exists despite AGENTS.md §5 documenting one — use `pnpm --dir frontend dev` directly (or `cd frontend && pnpm dev`).

## Launch

1. DB: `docker compose up -d db` (check first with `docker ps` — often already running).
2. Backend: `cd backend && uv run uvicorn private_rag_apps.api.main:app --port 8000` (background it: `(... &) ; sleep 3`). Health check: `curl localhost:8000/health`.
3. Frontend: `cd frontend && pnpm dev --port 3000` (background it similarly). Check: `curl -o /dev/null -w "%{http_code}" http://localhost:3000/`.

## Drive it

- Chat UI: `http://localhost:3000/` (assistant-ui chat + thread list sidebar).
- Data management: `http://localhost:3000/sources`.
- Conversation REST surface (what the frontend's `thread-adapter.ts`/`chat-adapter.ts` call client-side, proxied via `next.config.ts` rewrites `/api/* → 127.0.0.1:8000/api/*`):
  - `POST /api/conversations` → `{id, title}`
  - `GET /api/conversations` → `[{id, title, updated_at}]`
  - `GET /api/conversations/{id}` → `{id, title, messages: [{id, role, content, citations, created_at}]}` (`citations` is `null` on user messages, not omitted — matters for `?? []` handling)
  - `POST /api/chat` → SSE (`event: token|citations|error|done`)
  - Hit these directly with curl through **port 3000** (not 8000) to exercise the real Next.js rewrite path a browser would take.

## Gotchas

- `/api/chat` needs real `OPENAI_API_KEY`/`VOYAGE_API_KEY` in `backend/.env` to actually generate (the app calls OpenAI only — `generation/generator.py` imports `openai`, never `anthropic` — despite older docs/notes in this repo referring to `ANTHROPIC_API_KEY`). As of 2026-07-12 this repo's `backend/.env` had `EMBED_MODEL=` blank, which makes every chat request 500 (`voyageai.error.InvalidRequestError: Model  is not supported`) — double check `EMBED_MODEL` is a real Voyage model id (e.g. `voyage-4-lite`), not blank or a truncated value. To test conversation/history endpoints without paying for LLM calls or fixing that config, seed message rows directly via `private_rag_apps.models.rag.Message`/`Conversation` + `SessionLocal()` instead of going through `/api/chat` — clean them back up afterward (`db.query(Message).filter(...).delete()`), since they're synthetic.
- No browser-automation tool is available in this environment (no Playwright/DevTools MCP) — GUI/pixel-level checks (does a component actually render, is a button really gone from the DOM) can't be captured directly. Curl-based checks only prove the SSR shell and the REST/SSE contract, not client-side React behavior. Flag this gap explicitly rather than claiming a full visual PASS; ask the user to spot-check in a real browser if the change is UI-rendering-sensitive.
- Frontend has Vitest (`pnpm test`) as of 2026-07-12 — added because there was no test runner at all before. Use it for adapter/logic-level TDD; it does not replace running the app.
