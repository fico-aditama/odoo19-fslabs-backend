"""
Long-term memory via mem0 (self-hosted) — biar agent ingat tanpa kirim ulang semua
history tiap turn (hemat token). Provider LLM+embedder configurable dari Odoo:
ollama (lokal, gratis) / gemini / openai / custom.

Kalau mem0 tidak terpasang / dimatikan, fungsi jadi no-op (agent tetap jalan).
"""
import os
import config

_mem = None
_enabled = None


def _build_config():
    """Susun config mem0 dari REMOTE (ditarik dari Odoo)."""
    prov = (config.REMOTE.get("mem0_provider") or "openai").lower()
    model = config.REMOTE.get("mem0_model") or ""
    base = {}

    if prov == "ollama":
        base = {
            "llm": {"provider": "ollama",
                    "config": {"model": model or "llama3.1", "ollama_base_url":
                               config.REMOTE.get("ollama_url") or "http://localhost:11434"}},
            "embedder": {"provider": "ollama",
                         "config": {"model": config.REMOTE.get("mem0_embed") or "nomic-embed-text",
                                    "ollama_base_url": config.REMOTE.get("ollama_url") or "http://localhost:11434"}},
        }
    elif prov == "gemini":
        base = {
            "llm": {"provider": "gemini", "config": {"model": model or "gemini-2.0-flash"}},
            "embedder": {"provider": "gemini", "config": {"model": "models/text-embedding-004"}},
        }
    elif prov == "custom":
        # openai-compatible endpoint (mis. vLLM/LM Studio)
        base = {
            "llm": {"provider": "openai", "config": {"model": model or "gpt-4o-mini",
                    "openai_base_url": config.REMOTE.get("custom_base_url") or ""}},
            "embedder": {"provider": "openai", "config": {"model": config.REMOTE.get("mem0_embed") or "text-embedding-3-small"}},
        }
    else:  # openai
        base = {
            "llm": {"provider": "openai", "config": {"model": model or "gpt-4o-mini"}},
            "embedder": {"provider": "openai", "config": {"model": config.REMOTE.get("mem0_embed") or "text-embedding-3-small"}},
        }
    # simpan vektor mem0 di chroma lokal terpisah
    base["vector_store"] = {"provider": "chroma",
                            "config": {"collection_name": "cb_mem0",
                                       "path": os.path.join(config.CHROMA_DIR, "mem0")}}
    return base


def enabled():
    global _enabled
    if _enabled is None:
        _enabled = bool(config.REMOTE.get("mem0_enabled"))
    return _enabled


def _client():
    global _mem
    if _mem is None:
        from mem0 import Memory
        _mem = Memory.from_config(_build_config())
    return _mem


def reset():
    global _mem, _enabled
    _mem = None
    _enabled = None


def search(query, user_id, limit=5):
    if not enabled():
        return []
    try:
        res = _client().search(query=query, user_id=user_id, limit=limit)
        items = res.get("results", res) if isinstance(res, dict) else res
        return [m.get("memory", "") for m in (items or []) if m.get("memory")]
    except Exception as e:
        print(f"[mem0] search err: {e}")
        return []


def add(messages, user_id):
    if not enabled():
        return
    try:
        _client().add(messages, user_id=user_id)
    except Exception as e:
        print(f"[mem0] add err: {e}")
