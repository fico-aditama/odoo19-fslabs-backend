"""LangGraph ReAct agent (v1) + Langfuse. Model ditarik dari Odoo (lazy build)."""
from langgraph.prebuilt import create_react_agent
import config
import tools
import memory

_SYSTEM = (
    "You are a helpful personal assistant operating over the user's chat messages.\n"
    "Current time is: {now} (use this to compute reminder times).\n"
    "Rules:\n"
    "- For factual questions, call search_knowledge_base first and answer ONLY from it. "
    "If nothing relevant is found, say you will follow up rather than inventing facts.\n"
    "- For reminder/alarm/schedule requests, call create_reminder with an absolute "
    "'YYYY-MM-DD HH:MM:SS' time computed from the current time.\n"
    "- If asked to summarize/recap, summarize the recent conversation provided.\n"
    "- Keep replies short, natural, and in the user's language."
)

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = create_react_agent(
            config.model(), tools=[tools.search_knowledge_base, tools.create_reminder])
    return _agent


def reset_agent():
    global _agent
    _agent = None


def _handler():
    if not config.langfuse_on():
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception:
        return None


def run(message, history, now, session=None):
    tools.reset_request()
    uid = session or "cb"

    sys = _SYSTEM.format(now=now)
    # mem0: ambil memori relevan (hemat token — tak perlu kirim semua history)
    mems = memory.search(message, user_id=uid, limit=5)
    if mems:
        sys += "\n\nKnown facts about the user (long-term memory):\n- " + "\n- ".join(mems)

    msgs = [{"role": "system", "content": sys}]
    # history singkat saja (mem0 yang pegang konteks panjang)
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("is_from_me") else "user"
        who = h.get("sender") or ""
        msgs.append({"role": role, "content": f"{who}: {h.get('text','')}".strip(": ")})
    msgs.append({"role": "user", "content": message})

    cfg = {"recursion_limit": 8}
    handler = _handler()
    if handler:
        cfg["callbacks"] = [handler]
        cfg["metadata"] = {"langfuse_session_id": uid}

    result = get_agent().invoke({"messages": msgs}, config=cfg)
    reply = result["messages"][-1].content

    # simpan turn ke memori
    memory.add([{"role": "user", "content": message},
                {"role": "assistant", "content": reply}], user_id=uid)
    return reply, tools.get_actions()
