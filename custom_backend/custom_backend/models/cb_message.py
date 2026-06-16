from odoo import api, fields, models


class CbMessage(models.Model):
    """SEMUA pesan dari semua platform. Pembeda: platform + message_type."""
    _name = "cb.message"
    _description = "Social Message"
    _order = "message_time desc, id desc"
    _rec_name = "message_text"

    account_id = fields.Many2one("cb.account", required=True, ondelete="cascade", index=True)
    platform   = fields.Selection(related="account_id.platform", store=True, index=True)
    chat_id    = fields.Many2one("cb.chat", required=True, ondelete="cascade", index=True)
    chat_name  = fields.Char(related="chat_id.name", store=True)
    chat_type  = fields.Selection(related="chat_id.chat_type", store=True)

    ext_message_id = fields.Char(string="Platform Message ID", index=True)
    sender_jid  = fields.Char(string="Sender ID")
    sender_name = fields.Char(string="Sender")
    is_from_me  = fields.Boolean(string="Sent by Me", default=False)
    is_edited   = fields.Boolean(string="Edited", default=False)

    message_text = fields.Text(string="Message")
    message_type = fields.Selection([
        ("text", "Text"), ("image", "Image"), ("video", "Video"),
        ("audio", "Audio"), ("document", "Document"), ("sticker", "Sticker"),
        ("location", "Location"), ("live_location", "Live Location"),
        ("contact", "Contact"), ("poll", "Poll"), ("system", "System"),
        ("email", "Email"), ("other", "Other"),
    ], default="text", string="Type")
    message_time = fields.Datetime(string="Time", required=True, index=True)

    # attachment (universal)
    attachment_url      = fields.Char(string="Attachment URL")
    attachment_data     = fields.Binary(string="Attachment", attachment=True)
    attachment_mime     = fields.Char(string="MIME")
    attachment_filename = fields.Char(string="Filename")
    has_attachment      = fields.Boolean(compute="_compute_has_att", store=True)

    quoted_text_preview = fields.Char(string="Quoted")
    permalink           = fields.Char(string="Permalink")
    reply_to_ext_id     = fields.Char(string="Reply To Msg ID", index=True)
    is_forwarded        = fields.Boolean(string="Forwarded", default=False)
    forward_score       = fields.Integer(string="Forward Score")
    mentions            = fields.Char(string="Mentions")
    reactions           = fields.Char(string="Reactions")
    reply_text          = fields.Text(string="Reply")
    replied             = fields.Boolean(string="Replied", readonly=True)
    is_story            = fields.Boolean(string="Story/Status", default=False, index=True)
    is_deleted          = fields.Boolean(string="Deleted", default=False, index=True)
    is_view_once        = fields.Boolean(string="View Once", default=False)
    is_voice_note       = fields.Boolean(string="Voice Note", default=False)
    media_duration      = fields.Integer(string="Duration (s)")
    media_size          = fields.Integer(string="Size (bytes)")
    latitude            = fields.Float(string="Latitude", digits=(10, 7))
    longitude           = fields.Float(string="Longitude", digits=(10, 7))
    delivery_status     = fields.Selection([
        ("pending","Pending"),("sent","Sent"),("delivered","Delivered"),
        ("read","Read"),("played","Played"),("error","Error"),
    ], string="Delivery", index=True)
    read_by             = fields.Char(string="Read By")

    _unique_msg = models.Constraint(
        "UNIQUE(account_id, ext_message_id)", "Duplicate message per account.")

    @api.depends("attachment_url", "attachment_data")
    def _compute_has_att(self):
        for rec in self:
            rec.has_attachment = bool(rec.attachment_url or rec.attachment_data)

    def action_reply_email(self):
        from email.utils import parseaddr
        self.ensure_one()
        if self.platform != "email":
            from odoo.exceptions import UserError
            raise UserError("Reply email hanya untuk pesan platform email.")
        if not self.reply_text:
            from odoo.exceptions import UserError
            raise UserError("Tulis balasan dulu.")
        _, addr = parseaddr(self.sender_jid or "")
        if not addr:
            from odoo.exceptions import UserError
            raise UserError("Alamat pengirim tak terbaca.")
        subj = (self.message_text or "").split("]")[0].lstrip("[")[:120] or "(no subject)"
        if not subj.lower().startswith("re:"):
            subj = "Re: " + subj
        self.account_id.email_send(addr, subj, self.reply_text)
        self.replied = True
        return {"type":"ir.actions.client","tag":"display_notification",
                "params":{"message":f"Balasan terkirim ke {addr}","type":"success"}}

    @api.model
    def record_message(self, payload):
        account = self.env["cb.account"].sudo().search(
            [("bridge_token", "=", payload.get("session_token"))], limit=1)
        if not account:
            return {"error": "invalid_token"}

        chat_jid = payload.get("chat_jid")
        if not chat_jid:
            return {"error": "no_chat_jid"}

        chat = self.env["cb.chat"].sudo().search(
            [("account_id", "=", account.id), ("chat_jid", "=", chat_jid)], limit=1)
        chat_name = payload.get("chat_name") or chat_jid
        chat_type = payload.get("chat_type") or "dm"
        if not chat:
            chat = self.env["cb.chat"].sudo().create({
                "account_id": account.id, "chat_jid": chat_jid,
                "name": chat_name, "chat_type": chat_type,
            })
        elif chat_name and chat.name != chat_name:
            chat.sudo().write({"name": chat_name})

        if not chat.is_recording:
            return {"status": "skipped"}

        ext_id = payload.get("ext_message_id")
        existing = self.sudo().search(
            [("account_id", "=", account.id), ("ext_message_id", "=", ext_id)], limit=1)
        if existing:
            if payload.get("is_edited"):
                existing.sudo().write({
                    "message_text": payload.get("message_text"), "is_edited": True})
                return {"status": "updated", "id": existing.id}
            return {"status": "duplicate", "id": existing.id}

        vals = {
            "account_id": account.id, "chat_id": chat.id,
            "ext_message_id": ext_id,
            "sender_jid": payload.get("sender_jid"),
            "sender_name": payload.get("sender_name"),
            "is_from_me": payload.get("is_from_me", False),
            "is_edited": payload.get("is_edited", False),
            "message_type": payload.get("message_type", "text"),
            "message_text": payload.get("message_text"),
            "message_time": payload.get("message_time"),
            "attachment_url": payload.get("attachment_url"),
            "attachment_mime": payload.get("attachment_mime"),
            "attachment_filename": payload.get("attachment_filename"),
            "quoted_text_preview": payload.get("quoted_text_preview"),
            "permalink": payload.get("permalink"),
            "reply_to_ext_id": payload.get("reply_to_ext_id"),
            "is_forwarded": payload.get("is_forwarded", False),
            "forward_score": payload.get("forward_score", 0),
            "mentions": payload.get("mentions"),
            "reactions": payload.get("reactions"),
            "is_story": payload.get("is_story", False),
            "is_view_once": payload.get("is_view_once", False),
            "is_voice_note": payload.get("is_voice_note", False),
            "media_duration": payload.get("media_duration", 0),
            "media_size": payload.get("media_size", 0),
            "latitude": payload.get("latitude", 0.0),
            "longitude": payload.get("longitude", 0.0),
        }
        if payload.get("attachment_data"):
            vals["attachment_data"] = payload["attachment_data"]

        msg = self.sudo().create(vals)
        chat.sudo().write({
            "last_message_at": vals["message_time"],
            "last_message_preview": (vals.get("message_text") or f"[{vals['message_type']}]")[:80],
        })
        # notifikasi "ding" pesan masuk (bukan pesan sendiri)
        try:
            if not msg.is_from_me:
                self.sudo()._notify_ding(account, chat, msg)
        except Exception:
            pass
        # trigger auto-reply (aman: semua guard di dalam _maybe_autoreply)
        try:
            self.sudo()._maybe_autoreply(account, chat, msg)
        except Exception:
            pass  # auto-reply tidak boleh ganggu recording
        return {"status": "ok", "id": msg.id}

    @api.model
    def mark_deleted(self, payload):
        """Pesan di-revoke (hapus untuk semua) → tandai deleted."""
        account = self.env["cb.account"].sudo().search(
            [("bridge_token", "=", payload.get("session_token"))], limit=1)
        if not account:
            return {"error": "invalid_token"}
        msg = self.sudo().search([
            ("account_id", "=", account.id),
            ("ext_message_id", "=", payload.get("ext_message_id"))], limit=1)
        if msg:
            msg.write({"is_deleted": True})
            return {"status": "ok"}
        return {"status": "msg_not_found"}

    @api.model
    def update_status(self, payload):
        """Update read/unread (delivery) status pesan."""
        account = self.env["cb.account"].sudo().search(
            [("bridge_token", "=", payload.get("session_token"))], limit=1)
        if not account:
            return {"error": "invalid_token"}
        msg = self.sudo().search([
            ("account_id", "=", account.id),
            ("ext_message_id", "=", payload.get("ext_message_id"))], limit=1)
        if not msg:
            return {"status": "msg_not_found"}
        vals = {}
        if payload.get("delivery_status"):
            vals["delivery_status"] = payload["delivery_status"]
        if payload.get("read_by"):
            prev = msg.read_by or ""
            who = payload["read_by"]
            if who not in prev:
                vals["read_by"] = (prev + ", " + who).strip(", ") if prev else who
        if vals:
            msg.write(vals)
        return {"status": "ok"}

    @api.model
    def update_reaction(self, payload):
        """Reaction sering datang sebagai event terpisah → update pesan terkait."""
        account = self.env["cb.account"].sudo().search(
            [("bridge_token", "=", payload.get("session_token"))], limit=1)
        if not account:
            return {"error": "invalid_token"}
        msg = self.sudo().search([
            ("account_id", "=", account.id),
            ("ext_message_id", "=", payload.get("ext_message_id"))], limit=1)
        if not msg:
            return {"status": "msg_not_found"}
        msg.write({"reactions": payload.get("reactions")})
        return {"status": "ok"}

    @api.model
    def _notify_ding(self, account, chat, msg):
        """Push bus notification → frontend bunyi 'ding' + toast."""
        payload = {
            "platform": account.platform,
            "chat": chat.name,
            "sender": msg.sender_name or "",
            "preview": (msg.message_text or f"[{msg.message_type}]")[:80],
        }
        # kirim ke channel global 'cb_ding'
        self.env["bus.bus"]._sendone("cb_ding", "cb_ding", payload)

    @api.model
    def _maybe_autoreply(self, account, chat, msg):
        # GUARD 8: jangan bales pesan sendiri
        if msg.is_from_me:
            return
        # hanya WhatsApp
        if account.platform != "whatsapp":
            return
        # GUARD 5: hormati privacy exclude
        if chat.exclude_from_ai:
            return
        rules = self.env["cb.autoreply.rule"].sudo().search([
            ("active", "=", True), ("account_id", "=", account.id)])
        for rule in rules:
            # GUARD 6: grup — hanya kalau reply_groups ON
            if chat.chat_type in ("group", "channel"):
                if not rule.reply_groups:
                    continue
                # kalau group_mention_only: hanya balas kalau akun di-tag
                if rule.group_mention_only:
                    ident = (account.phone or account.username or "").replace("+", "")
                    blob = ((msg.mentions or "") + " " + (msg.message_text or "")).replace("+", "")
                    if not ident or ident not in blob:
                        continue
            # GUARD 1: whitelist
            if rule.scope == "whitelist" and chat not in rule.chat_ids:
                continue
            if rule.scope == "all_dm" and chat.chat_type != "dm":
                continue
            # GUARD 7: rate limit per chat per hari
            from datetime import datetime, time as _t
            start = datetime.combine(fields.Date.today(), _t.min)
            sent_today = self.env["cb.autoreply.log"].sudo().search_count([
                ("rule_id", "=", rule.id), ("chat_id", "=", chat.id),
                ("state", "in", ("sent", "draft")),
                ("create_date", ">=", fields.Datetime.to_string(start)),
            ])
            if rule.max_per_chat_per_day and sent_today >= rule.max_per_chat_per_day:
                continue
            # buat log pending → diproses cron
            self.env["cb.autoreply.log"].sudo().create({
                "rule_id": rule.id, "account_id": account.id, "chat_id": chat.id,
                "incoming_message_id": msg.id, "incoming_text": msg.message_text or "",
                "state": "pending",
            })
            break  # 1 rule match cukup
