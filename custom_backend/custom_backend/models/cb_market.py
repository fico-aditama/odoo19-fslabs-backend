"""
Market monitor: Gold, Forex (USD/IDR), Saham Indo (IDX) — live via Yahoo Finance v8/chart.
Catatan (verified 2026): v7/quote diblokir (401); v8/chart masih jalan tanpa crumb/cookie.
Ekstraksi harga dari chart.result[0].meta.regularMarketPrice.
"""
import json
import urllib.request
import urllib.parse
from odoo import api, fields, models
from odoo.exceptions import UserError

YF = "https://query1.finance.yahoo.com/v8/finance/chart/"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com",
}


class CbMarketSymbol(models.Model):
    _name = "cb.market.symbol"
    _description = "Market Symbol"
    _order = "category, sequence, id"

    name = fields.Char(string="Label", required=True)
    sequence = fields.Integer(default=10)
    symbol = fields.Char(string="Yahoo Symbol", required=True,
                         help="Gold: GC=F — USD/IDR: USDIDR=X — Saham Indo: BBCA.JK")
    category = fields.Selection([
        ("gold", "Gold / Komoditas"),
        ("forex", "Forex / Mata Uang"),
        ("stock_id", "Saham Indonesia (IDX)"),
        ("other", "Lainnya"),
    ], default="other", required=True, string="Category")
    active = fields.Boolean(default=True)

    last_price   = fields.Float(string="Last Price", digits=(16, 4), readonly=True)
    prev_close   = fields.Float(string="Prev Close", digits=(16, 4), readonly=True)
    change       = fields.Float(string="Change", digits=(16, 4), readonly=True)
    change_pct   = fields.Float(string="Change %", digits=(16, 2), readonly=True)
    currency     = fields.Char(string="Currency", readonly=True)
    last_update  = fields.Datetime(readonly=True)
    status       = fields.Selection([("idle","Idle"),("ok","OK"),("error","Error")],
                                    default="idle", readonly=True)
    last_error   = fields.Char(readonly=True)

    quote_ids = fields.One2many("cb.market.quote", "symbol_id", string="History")
    # alert ke WA
    alert_enabled = fields.Boolean(string="Alert to WhatsApp")
    alert_account_id = fields.Many2one("cb.account", string="WA Account",
                                       domain=[("platform", "=", "whatsapp")])
    alert_to = fields.Char(string="Send To (number/jid)")
    alert_threshold_pct = fields.Float(string="Alert if |change%| >=", default=1.0)
    last_alert = fields.Datetime(readonly=True)

    def action_refresh(self):
        for rec in self:
            rec._fetch()
        return True

    def _fetch(self):
        self.ensure_one()
        url = f"{YF}{urllib.parse.quote(self.symbol)}?interval=1d&range=1d"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0
            cur   = meta.get("currency") or ""
            if price is None:
                raise ValueError("regularMarketPrice kosong")
            change = price - prev if prev else 0.0
            pct = (change / prev * 100.0) if prev else 0.0
            self.write({
                "last_price": price, "prev_close": prev,
                "change": change, "change_pct": pct, "currency": cur,
                "last_update": fields.Datetime.now(), "status": "ok", "last_error": False,
            })
            # simpan snapshot history
            self.env["cb.market.quote"].sudo().create({
                "symbol_id": self.id, "price": price, "currency": cur,
            })
            # alert ke WA kalau perubahan signifikan (throttle 4 jam)
            self._maybe_alert(price, pct, cur)
        except Exception as e:
            self.write({"status": "error", "last_error": str(e)[:200]})

    def _maybe_alert(self, price, pct, cur):
        if not (self.alert_enabled and self.alert_account_id and self.alert_to):
            return
        if abs(pct) < (self.alert_threshold_pct or 1.0):
            return
        # throttle: sekali per 4 jam
        if self.last_alert:
            delta = fields.Datetime.now() - self.last_alert
            if delta.total_seconds() < 4 * 3600:
                return
        arrow = "📈" if pct >= 0 else "📉"
        text = f"{arrow} {self.name} ({self.symbol})\n{price:,.2f} {cur} ({pct:+.2f}%)"
        try:
            self.alert_account_id.send_whatsapp(self.alert_to, text)
            self.last_alert = fields.Datetime.now()
        except Exception as e:
            self.last_error = f"alert gagal: {str(e)[:120]}"

    @api.model
    def cron_refresh_all(self):
        for s in self.search([("active", "=", True)]):
            s._fetch()


class CbMarketQuote(models.Model):
    _name = "cb.market.quote"
    _description = "Market Quote Snapshot"
    _order = "create_date desc"

    symbol_id = fields.Many2one("cb.market.symbol", required=True, ondelete="cascade", index=True)
    price = fields.Float(digits=(16, 4))
    currency = fields.Char()
