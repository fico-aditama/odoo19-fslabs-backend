from odoo import api, fields, models


class CbChat(models.Model):
    """Chat/group/channel/thread dari semua platform."""
    _name = "cb.chat"
    _description = "Social Chat"
    _order = "last_message_at desc"

    account_id = fields.Many2one("cb.account", required=True, ondelete="cascade", index=True)
    platform   = fields.Selection(related="account_id.platform", store=True, index=True)
    chat_jid   = fields.Char(string="Chat ID", required=True, index=True)
    name       = fields.Char(string="Chat Name", required=True)
    chat_type  = fields.Selection([
        ("dm",      "Direct / Private"),
        ("group",   "Group"),
        ("channel", "Channel"),
    ], default="dm", string="Type")
    is_recording = fields.Boolean(string="Recording", default=True)
    exclude_from_ai = fields.Boolean(string="Exclude from AI (Privacy)", default=False,
        help="Kalau dicentang, chat ini TIDAK ikut di-summarize / auto-reply AI.")
    is_archived = fields.Boolean(string="Archived", default=False)
    is_pinned   = fields.Boolean(string="Pinned", default=False)
    is_muted    = fields.Boolean(string="Muted", default=False)
    unread_count = fields.Integer(string="Unread", default=0)

    message_ids   = fields.One2many("cb.message", "chat_id", string="Messages")
    message_count = fields.Integer(compute="_compute_count", store=True)
    last_message_at = fields.Datetime(index=True)
    last_message_preview = fields.Char()

    _unique_chat = models.Constraint(
        "UNIQUE(account_id, chat_jid)", "Chat unik per account.")

    @api.depends("message_ids")
    def _compute_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    @api.model
    def update_state(self, account, jid, vals):
        chat = self.sudo().search([("account_id","=",account.id),("chat_jid","=",jid)], limit=1)
        if chat:
            chat.write(vals)
        return chat

    def action_view_messages(self):
        return {"type": "ir.actions.act_window", "name": f"Messages — {self.name}",
                "res_model": "cb.message", "view_mode": "list,form",
                "domain": [("chat_id", "=", self.id)]}
