import logging
from odoo import http
from odoo.http import request
from odoo.fields import Datetime

_logger = logging.getLogger(__name__)


def _account(token):
    if not token:
        return None
    return request.env["cb.account"].sudo().search([("bridge_token", "=", token)], limit=1)


class CbWebhookController(http.Controller):

    @http.route("/cb/webhook", type="json", auth="public", methods=["POST"], csrf=False)
    def webhook(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        msgs = p.get("messages") or [p]
        results = []
        for m in msgs:
            m["session_token"] = acc.bridge_token
            results.append(request.env["cb.message"].sudo().record_message(m))
        return {"results": results}

    @http.route("/cb/reaction", type="json", auth="public", methods=["POST"], csrf=False)
    def reaction(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        items = p.get("reactions") or [p]
        for r in items:
            r["session_token"] = acc.bridge_token
            request.env["cb.message"].sudo().update_reaction(r)
        return {"status": "ok"}

    @http.route("/cb/receipt", type="json", auth="public", methods=["POST"], csrf=False)
    def receipt(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        for r in (p.get("receipts") or []):
            r["session_token"] = acc.bridge_token
            request.env["cb.message"].sudo().update_status(r)
        return {"status": "ok"}

    @http.route("/cb/chatstate", type="json", auth="public", methods=["POST"], csrf=False)
    def chatstate(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        Chat = request.env["cb.chat"].sudo()
        for s in (p.get("chats") or []):
            jid = s.get("chat_jid")
            if not jid:
                continue
            vals = {}
            for k in ("is_archived", "is_pinned", "is_muted", "unread_count"):
                if k in s:
                    vals[k] = s[k]
            if vals:
                Chat.update_state(acc, jid, vals)
        return {"status": "ok"}

    @http.route("/cb/profile", type="json", auth="public", methods=["POST"], csrf=False)
    def profile(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        Contact = request.env["cb.contact"].sudo()
        for pr in (p.get("profiles") or []):
            if pr.get("jid"):
                Contact.update_profile(acc, pr["jid"], pr)
        return {"status": "ok"}

    @http.route("/cb/deleted", type="json", auth="public", methods=["POST"], csrf=False)
    def deleted(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        p["session_token"] = acc.bridge_token
        request.env["cb.message"].sudo().mark_deleted(p)
        return {"status": "ok"}

    @http.route("/cb/presence", type="json", auth="public", methods=["POST"], csrf=False)
    def presence(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        Contact = request.env["cb.contact"].sudo()
        for pr in (p.get("presences") or []):
            if pr.get("jid"):
                Contact.update_presence(acc, pr["jid"], pr.get("presence"), pr.get("last_seen"))
        return {"status": "ok"}

    @http.route("/cb/status", type="json", auth="public", methods=["POST"], csrf=False)
    def status(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        vals = {"last_ping": Datetime.now()}
        st = p.get("status")
        valid = ["connecting","waiting_qr","waiting_code","waiting_password",
                 "waiting_challenge","connected","disconnected","auth_failure"]
        if st in valid:
            vals["status"] = st
        if st == "waiting_qr":
            vals["qr_code_data"] = p.get("qr", "")
            if p.get("qr_image"):
                vals["qr_image"] = p["qr_image"]  # base64 PNG (tanpa prefix data:)
        elif st == "connected":
            vals["qr_code_data"] = False
            vals["qr_image"] = False
            vals["connected_at"] = Datetime.now()
            if p.get("username"): vals["username"] = p["username"]
        sl = acc.sudo()
        sl.write(vals)
        return {"status": "ok"}

    @http.route("/cb/chats/sync", type="json", auth="public", methods=["POST"], csrf=False)
    def chats_sync(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        n = 0
        Chat = request.env["cb.chat"].sudo()
        for c in p.get("chats", []):
            jid = c.get("chat_jid")
            if not jid:
                continue
            chat = Chat.search([("account_id", "=", acc.id), ("chat_jid", "=", jid)], limit=1)
            vals = {"account_id": acc.id, "chat_jid": jid,
                    "name": c.get("name") or jid, "chat_type": c.get("chat_type") or "dm"}
            if not chat:
                Chat.create(vals)
            else:
                chat.write({"name": vals["name"], "chat_type": vals["chat_type"]})
            n += 1
        return {"status": "ok", "chats_synced": n}

    @http.route("/cb/contacts/sync", type="json", auth="public", methods=["POST"], csrf=False)
    def contacts_sync(self):
        p = request.get_json_data()
        acc = _account(p.get("session_token"))
        if not acc:
            return {"error": "unauthorized"}
        Contact = request.env["cb.contact"].sudo()
        n = 0
        for c in p.get("contacts", []):
            if c.get("jid"):
                Contact.upsert(acc, c["jid"], c.get("name"))
                n += 1
        return {"status": "ok", "contacts_synced": n}

    @http.route("/cb/bridge/accounts", type="json", auth="public", methods=["POST"], csrf=False)
    def bridge_accounts(self):
        p = request.get_json_data()
        s = request.env["cb.settings"].sudo().search([], order="id asc", limit=1)
        if (s.bridge_secret or "") and p.get("bridge_secret") != s.bridge_secret:
            return {"error": "unauthorized"}
        return {"accounts": request.env["cb.account"].sudo().get_active_accounts()}
