"""
Auto-reply rules + log. Fokus: SAFETY (8 lapis guard, lihat README).
Hanya WhatsApp untuk sekarang.
"""
from datetime import datetime
from odoo import api, fields, models
from odoo.exceptions import UserError

DEFAULT_GUARD = (
    "You are an assistant replying to WhatsApp chats on behalf of the account owner. "
    "STRICT RULES:\n"
    "1. Answer ONLY based on the Knowledge Base below.\n"
    "2. If the question is NOT answered by the Knowledge Base, DO NOT make things up - "
    "reply exactly with the provided fallback sentence.\n"
    "3. Keep replies short, polite, and natural like a normal chat.\n"
    "4. Do not make promises, prices, or commitments not in the Knowledge Base.\n"
    "5. Do not discuss anything outside the message context."
)


class CbAutoreplyRule(models.Model):
    _name = "cb.autoreply.rule"
    _description = "Auto-Reply Rule"
    _order = "sequence, id"

    name = fields.Char(required=True, default="Auto-Reply Rule")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one("cb.account", required=True, ondelete="cascade",
                                 domain=[("platform", "=", "whatsapp")],
                                 help="Akun WhatsApp yang dipakai auto-reply.")

    mode = fields.Selection([
        ("draft_only", "Draft Only (generate, TIDAK kirim) — buat testing"),
        ("auto_send",  "Auto Send (langsung kirim)"),
    ], default="draft_only", required=True, string="Mode")

    scope = fields.Selection([
        ("whitelist", "Whitelist (hanya chat terpilih)"),
        ("all_dm",    "Semua DM (hati-hati!)"),
    ], default="whitelist", required=True, string="Scope")
    chat_ids = fields.Many2many("cb.chat", string="Whitelist Chats",
                                help="Hanya chat ini yang dibalas (kalau scope=whitelist). Boleh DM atau grup.")
    reply_groups = fields.Boolean(string="Reply in Groups", default=False,
                                  help="Aktifkan supaya bisa auto-reply di grup.")
    group_mention_only = fields.Boolean(string="Group: Reply Only When Tagged", default=True,
                                        help="Di grup, hanya balas kalau akun kamu di-tag/mention.")

    # Knowledge base
    use_kb = fields.Boolean(string="Use Knowledge Base", default=True)
    fallback_message = fields.Text(
        string="Fallback Message", required=True,
        default="Hi, I have received your message. I will get back to you with more details shortly.",
        help="Dikirim kalau AI tidak yakin / pertanyaan di luar Knowledge Base.")
    guard_prompt = fields.Text(string="Guard / System Prompt", default=DEFAULT_GUARD)

    # Out of office
    ooo_enabled = fields.Boolean(string="Out-of-Office Mode")
    ooo_message = fields.Text(string="OOO Message",
                              default="Hi, I am currently unavailable. I will respond once I am back. Thank you!")
    ooo_from = fields.Datetime(string="OOO From")
    ooo_to   = fields.Datetime(string="OOO To")

    # anti-spam
    max_per_chat_per_day = fields.Integer(string="Max Replies / Chat / Day", default=5)

    log_count = fields.Integer(compute="_compute_log_count")

    def _compute_log_count(self):
        for rec in self:
            rec.log_count = self.env["cb.autoreply.log"].search_count([("rule_id", "=", rec.id)])

    def action_view_logs(self):
        return {"type": "ir.actions.act_window", "name": "Auto-Reply Logs",
                "res_model": "cb.autoreply.log", "view_mode": "list,form",
                "domain": [("rule_id", "=", self.id)]}

    def _is_ooo_now(self):
        self.ensure_one()
        if not self.ooo_enabled:
            return False
        now = fields.Datetime.now()
        if self.ooo_from and now < self.ooo_from:
            return False
        if self.ooo_to and now > self.ooo_to:
            return False
        return True


class CbAutoreplyLog(models.Model):
    _name = "cb.autoreply.log"
    _description = "Auto-Reply Log"
    _order = "create_date desc"

    rule_id = fields.Many2one("cb.autoreply.rule", ondelete="cascade", index=True)
    account_id = fields.Many2one("cb.account", ondelete="cascade", index=True)
    chat_id = fields.Many2one("cb.chat", ondelete="cascade", index=True)
    incoming_message_id = fields.Many2one("cb.message", ondelete="set null")
    incoming_text = fields.Text(string="Incoming")
    generated_reply = fields.Text(string="Reply")
    used_kb = fields.Boolean(string="Used KB")
    is_fallback = fields.Boolean(string="Fallback Used")
    state = fields.Selection([
        ("pending", "Pending"),
        ("draft",   "Draft (not sent)"),
        ("sent",    "Sent"),
        ("skipped", "Skipped"),
        ("failed",  "Failed"),
    ], default="pending", index=True)
    error = fields.Char()

    def action_send_now(self):
        """Manual kirim draft (approval)."""
        for rec in self:
            if rec.state not in ("draft", "failed"):
                continue
            rec._send()

    def _send(self):
        self.ensure_one()
        try:
            self.account_id._call_bridge("/cb/bridge/send", {
                "token": self.account_id.bridge_token,
                "to": self.chat_id.chat_jid,
                "text": self.generated_reply or "",
            })
            self.state = "sent"
        except Exception as e:
            self.state = "failed"
            self.error = str(e)[:300]

    # ── PROCESSOR (dipanggil cron) ──
    @api.model
    def cron_process(self):
        pending = self.search([("state", "=", "pending")], limit=50)
        for log in pending:
            try:
                log._process()
            except Exception as e:
                log.state = "failed"
                log.error = str(e)[:300]

    def _process(self):
        self.ensure_one()
        rule = self.rule_id
        if not rule or not rule.active:
            self.state = "skipped"; self.error = "rule inactive"; return

        # OOO mode → kirim pesan OOO, skip AI
        if rule._is_ooo_now():
            self.generated_reply = rule.ooo_message or ""
            self.is_fallback = True
            self._finalize(rule)
            return

        text = self.incoming_text or ""
        reply = None
        used_kb = False

        if rule.use_kb:
            kb_hits = self.env["cb.kb.entry"].match(text)
            if kb_hits:
                used_kb = True
                kb_text = "\n\n".join([f"[{e.name}]\n{e.content}" for e in kb_hits])
                cfg = self.env["cb.ai.config"]._get()
                prompt = (
                    f"KNOWLEDGE BASE:\n{kb_text}\n\n"
                    f"PESAN MASUK dari {self.chat_id.name}:\n{text}\n\n"
                    f"KALIMAT FALLBACK (pakai ini kalau tidak terjawab KB):\n{rule.fallback_message}\n\n"
                    "Balas pesan masuk sesuai aturan."
                )
                try:
                    reply = cfg._call_ai(prompt, system_prompt=rule.guard_prompt)
                except Exception as e:
                    self.state = "failed"; self.error = f"AI: {e}"; return
            else:
                # tidak ada KB match → fallback (TIDAK panggil AI, anti ngarang)
                reply = rule.fallback_message
                self.is_fallback = True
        else:
            reply = rule.fallback_message
            self.is_fallback = True

        self.generated_reply = reply
        self.used_kb = used_kb
        self._finalize(rule)

    def _finalize(self, rule):
        if rule.mode == "draft_only":
            self.state = "draft"     # tidak kirim, nunggu approval manual
        else:
            self._send()
