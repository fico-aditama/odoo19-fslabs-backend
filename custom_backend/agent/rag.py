"""RAG: ChromaDB + embeddings. Config (collection/embed model) ditarik dari Odoo."""
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
import config

_store = None


def get_store():
    global _store
    if _store is None:
        _store = Chroma(
            collection_name=config.collection(),
            embedding_function=OpenAIEmbeddings(model=config.embed_model()),
            persist_directory=config.CHROMA_DIR,
        )
    return _store


def reset_store():
    global _store
    _store = None


def index_kb(entries):
    store = get_store()
    ids, docs = [], []
    for e in entries:
        eid = str(e.get("id") or e.get("topic"))
        text = f"{e.get('topic','')}\nKeywords: {e.get('keywords','')}\n{e.get('content','')}"
        ids.append(eid)
        docs.append(Document(page_content=text, metadata={"topic": e.get("topic", ""), "id": eid}))
    if not docs:
        return 0
    try:
        store.delete(ids=ids)
    except Exception:
        pass
    store.add_documents(docs, ids=ids)
    return len(docs)


def search(query, k=4):
    try:
        return [h.page_content for h in get_store().similarity_search(query, k=k)]
    except Exception:
        return []
