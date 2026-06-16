"""
AI Summary — rangkum pesan penting hari ini, exclude chat privacy.
"""
from datetime import datetime, time
from odoo import api, fields, models
from odoo.exceptions import UserError

# batasi biar tidak blow context AI
MAX_MSG_PER_CHAT = 80
MAX_TOTAL_CHARS  = 24000


class CbAiSummary(models.Model):
    _name = "cb.ai.summary"
    _description = "AI Summary"
    _order = "create_date desc"

    name = fields.Char(string="Title", default=lambda s: f"Summary {fields.Date.today()}")
    summary_date = fields.Date(string="Date", default=fields.Date.today)
    scope = fields.Selection([
        ("today", "Today"),
        ("custom", "Custom Range"),
    ], default="today")
    date_from = fields.Datetime(string="From")
    date_to   = fields.Datetime(string="To")
    platform_filter = fields.Selection([
        ("all", "All Platforms"),
        ("whatsapp", "WhatsApp"), ("telegram", "Telegram"),
        ("discord", "Discord"), ("instagram", "Instagram"), ("threads", "Threads"),
    ], default="all", string="Platform")

    message_count = fields.Integer(string="Messages Analyzed", readonly=True)
    excluded_count = fields.Integer(string="Excluded (privacy)", readonly=True)
    summary_text = fields.Text(string="Summary", readonly=True)
    provider_used = fields.Char(readonly=True)

    def _range(self):
        self.ensure_one()
        if self.scope == "custom" and self.date_from and self.date_to:
            return self.date_from, self.date_to
        today = fields.Date.today()
        return (datetime.combine(today, time.min), datetime.combine(today, time.max))

    def action_generate(self):
        for rec in self:
            rec._generate()
        return True

    def _generate(self):
        self.ensure_one()
        cfg = self.env["cb.ai.config"]._get()
        dt_from, dt_to = self._range()

        # domain pesan: dalam range, chat TIDAK di-exclude
        domain = [
            ("message_time", ">=", fields.Datetime.to_string(dt_from)),
            ("message_time", "<=", fields.Datetime.to_string(dt_to)),
            ("chat_id.exclude_from_ai", "=", False),
        ]
        if self.platform_filter and self.platform_filter != "all":
            domain.append(("platform", "=", self.platform_filter))

        msgs = self.env["cb.message"].search(domain, order="chat_id, message_time asc")

        # hitung yang di-exclude (untuk transparansi)
        excl_domain = [
            ("message_time", ">=", fields.Datetime.to_string(dt_from)),
            ("message_time", "<=", fields.Datetime.to_string(dt_to)),
            ("chat_id.exclude_from_ai", "=", True),
        ]
        excluded = self.env["cb.message"].search_count(excl_domain)

        if not msgs:
            self.write({"summary_text": "(Tidak ada pesan untuk dirangkum di rentang ini.)",
                        "message_count": 0, "excluded_count": excluded})
            return

        # bangun transcript per chat (dibatasi)
        transcript = self._build_transcript(msgs)

        prompt = (f"Berikut percakapan dari {dt_from:%Y-%m-%d %H:%M} sampai {dt_to:%Y-%m-%d %H:%M}.\n\n"
                  f"{transcript}\n\n"
                  "Rangkum hal-hal PENTING saja, kelompokkan per chat.")
        try:
            result = cfg._call_ai(prompt, system_prompt=cfg.summary_prompt)
        except Exception as e:
            raise UserError(f"Gagal panggil AI: {e}")

        self.write({
            "summary_text": result,
            "message_count": len(msgs),
            "excluded_count": excluded,
            "provider_used": cfg.provider,
        })

    def _build_transcript(self, msgs):
        by_chat = {}
        for m in msgs:
            by_chat.setdefault(m.chat_id, []).append(m)
        chunks = []
        total = 0
        for chat, items in by_chat.items():
            items = items[-MAX_MSG_PER_CHAT:]   # ambil terakhir kalau kebanyakan
            header = f"\n### [{chat.platform}] {chat.name} ({chat.chat_type})\n"
            lines = []
            for m in items:
                who = "Saya" if m.is_from_me else (m.sender_name or "?")
                txt = (m.message_text or f"[{m.message_type}]").replace("\n", " ")
                lines.append(f"- {who}: {txt}")
            block = header + "\n".join(lines)
            if total + len(block) > MAX_TOTAL_CHARS:
                chunks.append("\n[...sebagian chat dipotong karena terlalu panjang...]")
                break
            chunks.append(block)
            total += len(block)
        return "\n".join(chunks)
