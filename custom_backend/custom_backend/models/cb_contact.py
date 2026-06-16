from odoo import api, fields, models


class CbContact(models.Model):
    _name = "cb.contact"
    _description = "Social Contact"
    _order = "name asc"

    account_id = fields.Many2one("cb.account", required=True, ondelete="cascade", index=True)
    platform   = fields.Selection(related="account_id.platform", store=True, index=True)
    jid  = fields.Char(string="ID", required=True, index=True)
    name = fields.Char(string="Name")
    # presence ("detective")
    presence = fields.Selection([
        ("available", "🟢 Online"),
        ("unavailable", "⚫ Offline"),
        ("composing", "✍️ Typing"),
        ("recording", "🎤 Recording"),
    ], string="Presence", readonly=True)
    last_seen = fields.Datetime(string="Last Seen", readonly=True)
    presence_updated = fields.Datetime(string="Presence Updated", readonly=True)
    # profil (scrape sedetail mungkin)
    about       = fields.Char(string="About / Status", readonly=True)
    avatar_url  = fields.Char(string="Profile Pic URL", readonly=True)
    is_business = fields.Boolean(string="Business", readonly=True)
    business_name = fields.Char(string="Business Name", readonly=True)
    phone_number  = fields.Char(string="Phone", readonly=True)

    _unique_contact = models.Constraint(
        "UNIQUE(account_id, jid)", "Contact unik per account.")

    @api.model
    def update_presence(self, account, jid, presence, last_seen=None):
        c = self.sudo().search([("account_id","=",account.id),("jid","=",jid)], limit=1)
        if not c:
            c = self.sudo().create({"account_id": account.id, "jid": jid, "name": jid})
        vals = {"presence": presence, "presence_updated": fields.Datetime.now()}
        if last_seen:
            vals["last_seen"] = last_seen
        c.sudo().write(vals)
        return c

    @api.model
    def update_profile(self, account, jid, vals):
        c = self.sudo().search([("account_id","=",account.id),("jid","=",jid)], limit=1)
        if not c:
            c = self.sudo().create({"account_id": account.id, "jid": jid, "name": vals.get("name") or jid})
        clean = {k: v for k, v in vals.items() if v}
        if clean:
            c.sudo().write(clean)
        return c

    @api.model
    def upsert(self, account, jid, name):
        c = self.sudo().search([("account_id", "=", account.id), ("jid", "=", jid)], limit=1)
        if not c:
            self.sudo().create({"account_id": account.id, "jid": jid, "name": name or jid})
        elif name and c.name != name:
            c.sudo().write({"name": name})
