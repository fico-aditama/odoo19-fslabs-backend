"""FastAPI service. Config ditarik dari Odoo saat startup (pola bridge)."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import config, rag, agent, memory

app = FastAPI(title="Custom Backend Agent")


@app.on_event("startup")
def _startup():
    config.fetch_remote()   # tarik LLM key/model/langfuse/chroma dari Odoo


def _auth(secret):
    if config.AGENT_SECRET and secret != config.AGENT_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")


class ChatIn(BaseModel):
    message: str
    history: List[Dict[str, Any]] = []
    now: str
    session: Optional[str] = None
    secret: Optional[str] = None


class IndexIn(BaseModel):
    entries: List[Dict[str, Any]]
    secret: Optional[str] = None


@app.get("/health")
def health():
    return {"ok": True, "model": config.model(), "langfuse": config.langfuse_on()}


@app.post("/agent/refresh")
def refresh(body: IndexIn):
    _auth(body.secret)
    ok = config.fetch_remote()
    agent.reset_agent(); rag.reset_store(); memory.reset()
    return {"reloaded": ok, "model": config.model()}


@app.post("/agent/chat")
def chat(body: ChatIn):
    _auth(body.secret)
    reply, actions = agent.run(body.message, body.history, body.now, body.session)
    return {"reply": reply, "actions": actions}


@app.post("/agent/index")
def index(body: IndexIn):
    _auth(body.secret)
    return {"indexed": rag.index_kb(body.entries)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.AGENT_PORT)
