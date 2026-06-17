"""Config minimal dari .env (kek bridge). Sisanya ditarik dari Odoo /cb/agent/config."""
import os
import requests
from dotenv import load_dotenv
load_dotenv()

ODOO_URL     = os.getenv("ODOO_URL", "http://localhost:8069").rstrip("/")
AGENT_SECRET = os.getenv("AGENT_SECRET", "")
AGENT_PORT   = int(os.getenv("AGENT_PORT", "8800"))
CHROMA_DIR   = os.getenv("CHROMA_DIR", "./chroma_db")

# diisi dari Odoo saat startup / refresh
REMOTE = {
    "agent_model": "openai:gpt-4o-mini",
    "embed_model": "text-embedding-3-small",
    "chroma_collection": "cb_kb",
}


def fetch_remote():
    """Tarik config dari Odoo (kek bridge tarik accounts). Set env utk LLM/Langfuse."""
    try:
        r = requests.post(f"{ODOO_URL}/cb/agent/config",
                          json={"secret": AGENT_SECRET}, timeout=20)
        data = r.json()
        # Odoo type=json membungkus di .result
        cfg = data.get("result", data)
        if not cfg or cfg.get("error"):
            print(f"[agent] gagal tarik config: {cfg}")
            return False
        REMOTE.update(cfg)
        # set env supaya langchain & langfuse otomatis kebaca
        if cfg.get("api_key"):
            os.environ["OPENAI_API_KEY"] = cfg["api_key"]   # provider openai
            if cfg.get("provider") == "gemini":
                os.environ["GOOGLE_API_KEY"] = cfg["api_key"]
        if cfg.get("langfuse_public_key"):
            os.environ["LANGFUSE_PUBLIC_KEY"] = cfg["langfuse_public_key"]
            os.environ["LANGFUSE_SECRET_KEY"] = cfg.get("langfuse_secret_key", "")
            os.environ["LANGFUSE_HOST"] = cfg.get("langfuse_host", "https://cloud.langfuse.com")
        print(f"[agent] config loaded from Odoo: model={REMOTE.get('agent_model')}")
        return True
    except Exception as e:
        print(f"[agent] fetch_remote error: {e}")
        return False


def model():       return REMOTE.get("agent_model", "openai:gpt-4o-mini")
def embed_model(): return REMOTE.get("embed_model", "text-embedding-3-small")
def collection():  return REMOTE.get("chroma_collection", "cb_kb")
def langfuse_on(): return bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
