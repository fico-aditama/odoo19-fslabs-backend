import secrets
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError

PLATFORMS = [
    ("whatsapp",  "WhatsApp"),
    ("telegram",  "Telegram"),
    ("discord",   "Discord"),
    ("instagram", "Instagram"),
    ("threads",   "Threads"),
    ("email",     "Email"),
]


class CbAccount(models.Model):
    """Satu akun di salah satu platform. Pembeda: field `platform`."""
    _name = "cb.account"
    _description = "Social Account"
    _order = "platform, create_date desc"

    name = fields.Char(string="Label", required=True, default="Account")
    platform = fields.Selection(PLATFORMS, required=True, string="Platform", index=True)
    bridge_token = fields.Char(string="Bridge Token", readonly=True, copy=False)

    status = fields.Selection([
        ("disconnected",      "Disconnected"),
        ("connecting",        "Connecting"),
        ("waiting_qr",        "Waiting QR"),
        ("waiting_code",      "Waiting OTP/Code"),
        ("waiting_password",  "Waiting 2FA"),
        ("waiting_challenge", "Waiting Verification"),
        ("connected",         "Connected"),
        ("auth_failure",      "Auth Failure"),
    ], default="disconnected", readonly=True, string="Status")

    # credential fields (dipakai sesuai platform)
    phone        = fields.Char(string="Phone (TG/WA)")
    username     = fields.Char(string="Username/IG user")
    password     = fields.Char(string="Password (IG)")
    token_secret = fields.Char(string="Token (Discord/Threads)",
                               help="Discord user token / Threads access token")
    extra_config = fields.Char(string="Extra (Threads mode / scrape user)")
    # ── Email (platform=email) ──
    imap_host = fields.Char(string="IMAP Host", default="imap.gmail.com")
    imap_port = fields.Integer(string="IMAP Port", default=993)
    imap_ssl  = fields.Boolean(string="IMAP SSL", default=True)
    smtp_host = fields.Char(string="SMTP Host", default="smtp.gmail.com")
    smtp_port = fields.Integer(string="SMTP Port", default=587)
    smtp_tls  = fields.Boolean(string="SMTP TLS", default=True)
    mail_folder = fields.Char(string="Folder", default="INBOX")
    mail_fetch_limit = fields.Integer(string="Fetch Limit", default=50)
    mail_last_uid = fields.Char(readonly=True)
    # WA "detective": lacak presence kontak (online/offline)
    track_presence = fields.Boolean(string="Track Presence (WA)", default=False,
        help="WA: subscribe status online/offline kontak. Hati-hati: noisy & hanya kalau privasi kontak mengizinkan.")
    download_media = fields.Boolean(string="Download Media (WA)", default=True,
        help="WA: unduh foto/video/voice/dokumen ke Odoo (bukan cuma metadata).")
    media_max_mb = fields.Integer(string="Max Media Size (MB)", default=16,
        help="Media lebih besar dari ini tidak diunduh (hindari payload raksasa).")
    scrape_profiles = fields.Boolean(string="Scrape Profiles (WA)", default=False,
        help="WA: ambil about/foto/business kontak. Berat (banyak API call) + throttled.")

    # interactive login
    qr_code_data  = fields.Text(string="QR Data", readonly=True)
    qr_image      = fields.Binary(string="QR Image", readonly=True, attachment=True)
    login_code    = fields.Char(string="OTP / Verification Code")
    login_password= fields.Char(string="2FA Password")

    connected_at = fields.Datetime(readonly=True)
    last_ping    = fields.Datetime(readonly=True)
    bridge_log   = fields.Text(readonly=True)

    chat_count    = fields.Integer(compute="_compute_stats", string="Chats")
    message_count = fields.Integer(compute="_compute_stats", string="Messages")

    @api.depends("name")
    def _compute_stats(self):
        for rec in self:
            rec.chat_count    = self.env["cb.chat"].search_count([("account_id", "=", rec.id)])
            rec.message_count = self.env["cb.message"].search_count([("account_id", "=", rec.id)])

    # ── bridge ────────────────────────────────────────────────────────────────
    def _settings(self):
        return self.env["cb.settings"].sudo().search([], order="id asc", limit=1)

    def _call_bridge(self, path, data=None):
        s = self._settings()
        url = (s.bridge_url or "http://localhost:3000").rstrip("/") + path
        try:
            resp = requests.post(url, json={"bridge_secret": s.bridge_secret or "", **(data or {})}, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise UserError(f"Bridge tidak bisa dihubungi: {e}\nURL: {url}")

    def _connect_payload(self):
        """payload sesuai platform."""
        self.ensure_one()
        base = {"token": self.bridge_token, "platform": self.platform}
        if self.platform in ("whatsapp",):
            base["track_presence"] = self.track_presence
            base["download_media"] = self.download_media
            base["media_max_mb"] = self.media_max_mb or 16
            base["scrape_profiles"] = self.scrape_profiles
        elif self.platform == "telegram":
            base["phone"] = self.phone or ""
        elif self.platform == "discord":
            base["discord_token"] = self.token_secret or ""
        elif self.platform == "instagram":
            base["username"] = self.username or ""
            base["password"] = self.password or ""
        elif self.platform == "threads":
            base["mode"] = self.extra_config or "api"
            base["access_token"] = self.token_secret or ""
            base["scrape_user"] = self.username or ""
        return base

    def action_connect(self):
        for rec in self:
            if not rec.bridge_token:
                rec.bridge_token = secrets.token_urlsafe(32)
            if rec.platform == "email":
                rec._email_test()       # email: tidak lewat bridge
                continue
            rec.status = "connecting"
            rec.qr_code_data = False
            rec.qr_image = False
            rec._call_bridge("/cb/bridge/connect", rec._connect_payload())

    # ── EMAIL (native IMAP/SMTP, tanpa bridge) ──
    def _imap(self):
        import imaplib
        self.ensure_one()
        conn = (imaplib.IMAP4_SSL(self.imap_host, self.imap_port) if self.imap_ssl
                else imaplib.IMAP4(self.imap_host, self.imap_port))
        conn.login(self.username, self.password)
        return conn

    def _email_test(self):
        self.ensure_one()
        try:
            c = self._imap(); c.select(self.mail_folder or "INBOX", readonly=True); c.logout()
            self.status = "connected"; self.connected_at = fields.Datetime.now()
        except Exception as e:
            self.status = "auth_failure"; self.bridge_log = f"IMAP gagal: {e}"

    def action_fetch_email(self):
        for rec in self:
            if rec.platform == "email":
                rec._fetch_email()

    def _fetch_email(self):
        import imaplib, email
        from email.header import decode_header, make_header
        self.ensure_one()
        def dec(v):
            if not v: return ""
            try: return str(make_header(decode_header(v)))
            except Exception: return str(v)
        try:
            c = self._imap(); c.select(self.mail_folder or "INBOX", readonly=True)
            if self.mail_last_uid:
                typ, data = c.uid("search", None, f"UID {int(self.mail_last_uid)+1}:*")
            else:
                typ, data = c.uid("search", None, "ALL")
            uids = data[0].split() if data and data[0] else []
            lim = self.mail_fetch_limit or 50
            uids = uids[-lim:] if len(uids) > lim else uids
            maxu = int(self.mail_last_uid or 0)
            Msg = self.env["cb.message"].sudo()
            for uid in uids:
                us = uid.decode()
                typ, md = c.uid("fetch", uid, "(BODY.PEEK[])")
                if typ != "OK" or not md or not md[0]: continue
                m = email.message_from_bytes(md[0][1])
                # body
                body = ""
                if m.is_multipart():
                    for part in m.walk():
                        if "attachment" in str(part.get("Content-Disposition") or ""): continue
                        if part.get_content_type() == "text/plain" and not body:
                            try: body = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", errors="replace")
                            except Exception: pass
                else:
                    try: body = m.get_payload(decode=True).decode(
                        m.get_content_charset() or "utf-8", errors="replace")
                    except Exception: body = str(m.get_payload())
                from_ = dec(m.get("From")); subj = dec(m.get("Subject"))
                try:
                    dt = email.utils.parsedate_to_datetime(m.get("Date"))
                    mt = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else fields.Datetime.now()
                except Exception: mt = fields.Datetime.now()
                # chat = pengirim (thread per sender)
                Msg.record_message({
                    "session_token": self.bridge_token,
                    "chat_jid": from_, "chat_name": from_, "chat_type": "dm",
                    "ext_message_id": m.get("Message-ID") or us,
                    "sender_jid": from_, "sender_name": from_,
                    "is_from_me": False,
                    "message_type": "email",
                    "message_text": f"[{subj}]\n\n{body[:20000]}",
                    "message_time": mt,
                })
                if int(us) > maxu: maxu = int(us)
            c.logout()
            self.write({"mail_last_uid": str(maxu) if maxu else self.mail_last_uid,
                        "last_ping": fields.Datetime.now(), "status": "connected"})
        except Exception as e:
            self.status = "auth_failure"; self.bridge_log = f"Fetch gagal: {e}"

    def email_send(self, to_addr, subject, body):
        import smtplib
        from email.mime.text import MIMEText
        from email.utils import formataddr
        self.ensure_one()
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = formataddr((self.name, self.username))
        msg["To"] = to_addr; msg["Subject"] = subject
        if self.smtp_tls:
            s = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30); s.starttls()
        else:
            s = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        s.login(self.username, self.password)
        s.sendmail(self.username, [to_addr], msg.as_string()); s.quit()
        return True

    def send_whatsapp(self, to, text):
        """Kirim pesan WA (dipakai feed/market alert). `to` boleh nomor atau jid."""
        self.ensure_one()
        if self.platform != "whatsapp":
            raise UserError("Akun ini bukan WhatsApp.")
        jid = (to or "").strip()
        if "@" not in jid:
            jid = jid.lstrip("+").replace(" ", "").replace("-", "") + "@s.whatsapp.net"
        return self._call_bridge("/cb/bridge/send", {
            "token": self.bridge_token, "to": jid, "text": text or ""})

    @api.model
    def cron_fetch_email(self):
        for acc in self.search([("platform","=","email"),("status","!=","disconnected")]):
            acc._fetch_email()

    def action_submit_code(self):
        for rec in self:
            if not rec.login_code:
                raise UserError("Masukkan kode dulu.")
            rec._call_bridge("/cb/bridge/code", {
                "token": rec.bridge_token, "code": rec.login_code})
            rec.login_code = False

    def action_submit_password(self):
        for rec in self:
            if not rec.login_password:
                raise UserError("Masukkan password dulu.")
            rec._call_bridge("/cb/bridge/password", {
                "token": rec.bridge_token, "password": rec.login_password})
            rec.login_password = False

    def action_disconnect(self):
        for rec in self:
            if rec.bridge_token:
                try:
                    rec._call_bridge("/cb/bridge/disconnect", {"token": rec.bridge_token})
                except Exception:
                    pass
            rec.status = "disconnected"
            rec.qr_code_data = False
            rec.qr_image = False

    def action_resync(self):
        for rec in self:
            if rec.platform == "email":
                rec._fetch_email(); continue
            if not rec.bridge_token:
                raise UserError("Connect dulu.")
            res = rec._call_bridge("/cb/bridge/resync", {"token": rec.bridge_token})
            rec.bridge_log = f"Re-sync: {res}"
        return {"type":"ir.actions.client","tag":"display_notification",
                "params":{"message":"Re-sync dikirim ke bridge","type":"success"}}

    def action_refresh_log(self):
        for rec in self:
            if not rec.bridge_token:
                continue
            try:
                r = rec._call_bridge("/cb/bridge/logs", {"token": rec.bridge_token})
                rec.bridge_log = r.get("logs") or "(kosong)"
            except Exception as e:
                rec.bridge_log = f"Error: {e}"

    def action_view_chats(self):
        return {"type": "ir.actions.act_window", "name": "Chats",
                "res_model": "cb.chat", "view_mode": "list,form",
                "domain": [("account_id", "=", self.id)]}

    def action_view_messages(self):
        return {"type": "ir.actions.act_window", "name": "Messages",
                "res_model": "cb.message", "view_mode": "list,form",
                "domain": [("account_id", "=", self.id)]}

    @api.model
    def get_active_accounts(self):
        accs = self.search([("status", "not in", ["disconnected"]), ("bridge_token", "!=", False)])
        return [a._connect_payload() for a in accs]
