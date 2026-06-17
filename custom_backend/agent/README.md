# Custom Backend — Agent Service (LangGraph + RAG + Langfuse)

"Otak" assistant. Odoo (thin client) menembak ke service ini; service menjalankan
agent ReAct (LangGraph v1), RAG via ChromaDB, dan tracing via Langfuse.

## Arsitektur
```
Odoo (cb.assistant)  --POST /agent/chat-->  FastAPI
                                              └─ LangGraph ReAct agent
                                                  ├─ tool: search_knowledge_base (RAG/Chroma)
                                                  └─ tool: create_reminder (emit action)
                                              └─ Langfuse callback (trace)
   <--{reply, actions}-- (Odoo eksekusi actions: buat cb.reminder)

Odoo (KB)            --POST /agent/index--> index KB ke Chroma (embeddings)
```

## Kontrak
**POST /agent/chat**
```json
{ "message": "remind me at 9 to eat", "now": "2026-06-16 08:00:00",
  "history": [{"sender":"John","text":"hi","is_from_me":false}], "session":"wa1", "secret":"..." }
```
→ `{ "reply": "Reminder scheduled...", "actions": [{"type":"reminder","remind_at":"2026-06-16 21:00:00","text":"eat"}] }`

**POST /agent/index**  `{ "entries":[{"id":1,"topic":"Harga","keywords":"harga","content":"..."}], "secret":"..." }`

## Setup
`.env` agent MINIMAL (pola bridge) — cuma ini:
```
ODOO_URL=http://localhost:8069
AGENT_SECRET=ganti_random_panjang
AGENT_PORT=8800
CHROMA_DIR=./chroma_db
```
Semua config lain (OpenAI key, model, embed model, Langfuse keys, Chroma collection)
diatur di **Odoo → AI Config**, dan ditarik service ini saat start (kek bridge tarik accounts).
```bash
cd agent
cp .env.example .env          # isi ODOO_URL + AGENT_SECRET (sama dgn Odoo)
pip install -r requirements.txt
python app.py                 # tarik config dari Odoo lalu listen :8800
```
Ubah config di Odoo → panggil `POST /agent/refresh {secret}` (atau restart) untuk reload.

## Di Odoo
AI Config → aktifkan **Agent (LangGraph)** → isi Agent URL (http://localhost:8800) + secret (sama dgn .env).
Lalu klik **Sync KB → Agent** untuk index knowledge base ke Chroma.
Auto-Reply rule → centang **Assistant Mode**.

## Catatan
- Reminder TIDAK dieksekusi di service — di-emit sebagai `action`, Odoo yang membuat `cb.reminder` + kirim WA.
- Ganti `AGENT_MODEL` ke gemini/anthropic sesuai langchain provider (mis. `anthropic:claude-3-7-sonnet-latest`).
- Tanpa Langfuse key, tracing dilewati otomatis.
- create_react_agent (langgraph.prebuilt) dipakai; bisa migrasi ke `langchain.create_agent` kalau mau middleware.
