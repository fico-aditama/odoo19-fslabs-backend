"""Tools untuk agent. Side-effect (reminder) di-emit sebagai 'action' yang dieksekusi Odoo."""
import contextvars
from langchain_core.tools import tool
import rag

_actions = contextvars.ContextVar("actions", default=None)


def reset_request():
    _actions.set([])


def get_actions():
    return _actions.get() or []


def _push(action):
    acts = _actions.get()
    if acts is None:
        acts = []
    acts.append(action)
    _actions.set(acts)


@tool
def search_knowledge_base(query: str) -> str:
    """Search the user's knowledge base for facts needed to answer. Always use this before answering factual questions."""
    hits = rag.search(query, k=4)
    return "\n\n".join(hits) if hits else "No relevant knowledge base entries found."


@tool
def create_reminder(text: str, remind_at: str) -> str:
    """Schedule a reminder for the user.
    text: what to remind about.
    remind_at: absolute time 'YYYY-MM-DD HH:MM:SS' computed from the current time given in the system prompt.
    Use this whenever the user asks to be reminded or to set an alarm/schedule."""
    _push({"type": "reminder", "remind_at": remind_at, "text": text})
    return f"Reminder scheduled for {remind_at}: {text}"
