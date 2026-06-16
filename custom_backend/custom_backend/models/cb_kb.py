"""Knowledge Base — kamu input info/Q&A di Odoo, dipakai grounding auto-reply."""
from odoo import api, fields, models


class CbKbEntry(models.Model):
    _name = "cb.kb.entry"
    _description = "Knowledge Base Entry"
    _order = "sequence, id"

    name = fields.Char(string="Topic / Question", required=True)
    sequence = fields.Integer(default=10)
    keywords = fields.Char(string="Keywords",
                           help="Kata kunci pemicu, pisah koma. mis: harga, pricing, biaya")
    content = fields.Text(string="Answer / Info", required=True,
                          help="Jawaban/info yang boleh dipakai AI untuk balas.")
    active = fields.Boolean(default=True)

    @api.model
    def match(self, text):
        """Cari KB entry yang relevan dengan text masuk (keyword overlap sederhana)."""
        if not text:
            return self.browse()
        low = text.lower()
        hits = self.browse()
        for e in self.search([("active", "=", True)]):
            kws = [k.strip().lower() for k in (e.keywords or "").split(",") if k.strip()]
            if not kws:
                continue
            if any(k in low for k in kws):
                hits |= e
        return hits
