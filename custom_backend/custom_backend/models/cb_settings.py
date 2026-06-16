import requests
from datetime import datetime
from odoo import api, fields, models


class CbSettings(models.Model):
    _name = "cb.settings"
    _description = "Custom Backend Settings"

    bridge_url    = fields.Char(string="Bridge URL", default="http://localhost:3000")
    bridge_secret = fields.Char(string="Bridge Secret")

    # ── monitoring ──
    bridge_health = fields.Selection([
        ("unknown", "Unknown"), ("up", "Up"), ("down", "Down"),
    ], default="unknown", readonly=True, string="Bridge Health")
    bridge_accounts = fields.Integer(string="Active Accounts (bridge)", readonly=True)
    last_check = fields.Datetime(readonly=True)
    last_error = fields.Char(readonly=True)
    response_ms = fields.Integer(string="Response (ms)", readonly=True)

    def action_health_check(self):
        for rec in self:
            rec._check()
        return True

    def _check(self):
        self.ensure_one()
        url = (self.bridge_url or "http://localhost:3000").rstrip("/") + "/health"
        t0 = datetime.now()
        try:
            resp = requests.get(url, timeout=8)
            ms = int((datetime.now() - t0).total_seconds() * 1000)
            if resp.status_code == 200:
                data = resp.json()
                self.write({"bridge_health": "up",
                            "bridge_accounts": data.get("accounts", 0),
                            "last_check": fields.Datetime.now(),
                            "last_error": False, "response_ms": ms})
            else:
                self.write({"bridge_health": "down", "last_check": fields.Datetime.now(),
                            "last_error": f"HTTP {resp.status_code}", "response_ms": ms})
        except Exception as e:
            self.write({"bridge_health": "down", "bridge_accounts": 0,
                        "last_check": fields.Datetime.now(),
                        "last_error": str(e)[:200], "response_ms": 0})

    @api.model
    def cron_health_check(self):
        s = self.search([], limit=1)
        if s:
            s._check()
