"""
Feed monitor: Google News + Medium (RSS, legal, native Python).
Parsing pakai stdlib xml.etree (tanpa dependency tambahan).
"""
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from odoo import api, fields, models
from odoo.exceptions import UserError

UA = "Mozilla/5.0 (compatible; OdooFeedBot/1.0)"


class CbFeedSource(models.Model):
    _name = "cb.feed.source"
    _description = "Feed Source"
    _order = "create_date desc"

    name = fields.Char(required=True)
    source_type = fields.Selection([
        ("google_news", "Google News"),
        ("medium", "Medium"),
        ("jobstreet", "Jobstreet (scrape)"),
        ("glints", "Glints (scrape)"),
        ("linkedin", "LinkedIn (scrape, rawan)"),
    ], required=True, default="google_news", string="Source")
    query = fields.Char(required=True,
                        help="Google News: keyword. Medium: @username atau tag:topik")
    active = fields.Boolean(default=True)
    last_poll = fields.Datetime(readonly=True)
    status = fields.Selection([("idle","Idle"),("ok","OK"),("error","Error")],
                              default="idle", readonly=True)
    last_error = fields.Char(readonly=True)
    item_count = fields.Integer(compute="_compute_count")
    # alert realtime ke WA
    alert_enabled = fields.Boolean(string="Alert to WhatsApp")
    alert_account_id = fields.Many2one("cb.account", string="WA Account",
                                       domain=[("platform", "=", "whatsapp")])
    alert_to = fields.Char(string="Send To (number/jid)",
                           help="Nomor WA (628xx) atau jid grup. Tiap item baru dikirim ke sini.")
    alert_max_per_poll = fields.Integer(string="Max Alerts/Poll", default=5)

    @api.depends("name")
    def _compute_count(self):
        for rec in self:
            rec.item_count = self.env["cb.feed.item"].search_count([("source_id","=",rec.id)])

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for r in recs:
            try: r._poll()      # langsung poll pas dibuat, tidak perlu manual
            except Exception: pass
        return recs

    def _send_alert(self, title, url, author=""):
        """Kirim 1 item baru ke WA (kalau alert aktif)."""
        if not self.alert_enabled or not self.alert_account_id or not self.alert_to:
            return
        label = dict(self._fields["source_type"].selection).get(self.source_type, self.source_type)
        text = f"🔔 [{label}] {self.name}\n\n{title}"
        if author: text += f"\n— {author}"
        if url: text += f"\n{url}"
        try:
            self.alert_account_id.send_whatsapp(self.alert_to, text)
        except Exception as e:
            self.write({"last_error": f"alert gagal: {str(e)[:120]}"})

    def _feed_url(self):
        self.ensure_one()
        q = (self.query or "").strip()
        if self.source_type == "google_news":
            from urllib.parse import quote
            return (f"https://news.google.com/rss/search?q={quote(q)}"
                    f"&hl=id&gl=ID&ceid=ID:id")
        # medium
        if q.startswith("@"):
            return f"https://medium.com/feed/{q}"
        if q.startswith("tag:"):
            return f"https://medium.com/feed/tag/{q[4:]}"
        return f"https://medium.com/feed/@{q}"

    def _fetch_xml(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def action_poll(self):
        for rec in self: rec._poll()
        return {"type":"ir.actions.client","tag":"display_notification",
                "params":{"message":"Poll selesai","type":"success"}}

    def _poll(self):
        self.ensure_one()
        if self.source_type in ("jobstreet", "glints", "linkedin"):
            return self._poll_scrape()
        try:
            raw = self._fetch_xml(self._feed_url())
            root = ET.fromstring(raw)
            # RSS 2.0: channel/item ; Atom: entry
            items = root.findall(".//item")
            n = 0
            Item = self.env["cb.feed.item"].sudo()
            for it in items:
                guid = (it.findtext("guid") or it.findtext("link") or "").strip()
                if not guid: continue
                if Item.search([("source_id","=",self.id),("guid","=",guid)], limit=1):
                    continue
                pub = it.findtext("pubDate")
                try: pubdt = parsedate_to_datetime(pub).strftime("%Y-%m-%d %H:%M:%S") if pub else None
                except Exception: pubdt = None
                # author: dc:creator namespace
                author = it.findtext("{http://purl.org/dc/elements/1.1/}creator") or ""
                desc = (it.findtext("description") or "")[:2000]
                title = (it.findtext("title") or "")[:300]
                link = (it.findtext("link") or "").strip()
                Item.create({
                    "source_id": self.id, "guid": guid, "title": title,
                    "url": link, "author": author[:120], "summary": desc, "published": pubdt,
                })
                if n < (self.alert_max_per_poll or 5):
                    self._send_alert(title, link, author[:120])
                n += 1
            self.write({"status":"ok","last_poll":fields.Datetime.now(),"last_error":False})
            return n
        except Exception as e:
            self.write({"status":"error","last_error":str(e)[:200]})
            return 0

    def _http_get(self, url):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", errors="replace")

    def _poll_scrape(self):
        """Best-effort scraping. Fragile — struktur web bisa berubah."""
        self.ensure_one()
        import re, json as _json
        from urllib.parse import quote
        q = (self.query or "").strip()
        Item = self.env["cb.feed.item"].sudo()
        n = 0
        try:
            if self.source_type == "jobstreet":
                url = f"https://www.jobstreet.co.id/id/{quote(q.replace(' ', '-'))}-jobs"
                html = self._http_get(url)
                # cari JSON job di __NEXT_DATA__ kalau ada, else regex judul
                jobs = []
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                if m:
                    try:
                        data = _json.loads(m.group(1))
                        jobs = self._dig_jobs(data)
                    except Exception:
                        jobs = []
                for j in jobs:
                    if not j.get("id"): continue
                    guid = f"jobstreet_{j['id']}"
                    if Item.search([("source_id","=",self.id),("guid","=",guid)], limit=1): continue
                    t = (j.get("title") or "")[:300]
                    Item.create({"source_id": self.id, "guid": guid,
                                 "title": t, "author": j.get("company") or "",
                                 "url": j.get("url") or "", "summary": j.get("location") or ""})
                    if n < (self.alert_max_per_poll or 5): self._send_alert(t, j.get("url") or "", j.get("company") or "")
                    n += 1

            elif self.source_type == "glints":
                url = f"https://glints.com/id/opportunities/jobs/explore?keyword={quote(q)}"
                html = self._http_get(url)
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                jobs = []
                if m:
                    try: jobs = self._dig_jobs(_json.loads(m.group(1)))
                    except Exception: jobs = []
                for j in jobs:
                    if not j.get("id"): continue
                    guid = f"glints_{j['id']}"
                    if Item.search([("source_id","=",self.id),("guid","=",guid)], limit=1): continue
                    t = (j.get("title") or "")[:300]
                    Item.create({"source_id": self.id, "guid": guid,
                                 "title": t, "author": j.get("company") or "",
                                 "url": j.get("url") or "", "summary": j.get("location") or ""})
                    if n < (self.alert_max_per_poll or 5): self._send_alert(t, j.get("url") or "", j.get("company") or "")
                    n += 1

            elif self.source_type == "linkedin":
                # endpoint guest (tanpa login) — rawan 429
                url = (f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
                       f"search?keywords={quote(q)}&start=0")
                html = self._http_get(url)
                # tiap job ada di <a class="base-card__full-link" href="...">
                cards = re.findall(
                    r'<a[^>]*base-card__full-link[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
                titles = re.findall(r'<span[^>]*sr-only[^>]*>\s*(.*?)\s*</span>', html, re.S)
                companies = re.findall(r'base-search-card__subtitle[^>]*>\s*(?:<a[^>]*>)?\s*(.*?)\s*<', html, re.S)
                for i, (href, _) in enumerate(cards):
                    link = href.split("?")[0]
                    guid = f"linkedin_{link}"
                    if Item.search([("source_id","=",self.id),("guid","=",guid)], limit=1): continue
                    title = (titles[i].strip() if i < len(titles) else "")[:300]
                    comp = (companies[i].strip() if i < len(companies) else "")
                    if not title: continue
                    Item.create({"source_id": self.id, "guid": guid, "title": title,
                                 "author": comp, "url": link, "summary": ""})
                    if n < (self.alert_max_per_poll or 5): self._send_alert(title, link, comp)
                    n += 1

            self.write({"status":"ok","last_poll":fields.Datetime.now(),"last_error":False})
            if n == 0:
                self.write({"last_error": "0 item (struktur web mungkin berubah / butuh JS render)"})
            return n
        except Exception as e:
            self.write({"status":"error","last_error":str(e)[:200]})
            return 0

    def _dig_jobs(self, node, out=None):
        """Cari objek job di JSON Next.js (heuristik)."""
        if out is None: out = []
        if isinstance(node, list):
            for x in node: self._dig_jobs(x, out)
            return out
        if isinstance(node, dict):
            jid = node.get("id")
            title = node.get("title") or node.get("jobTitle")
            comp = (node.get("company") or {}).get("name") if isinstance(node.get("company"), dict) else node.get("companyName")
            if jid and title and comp:
                loc = ""
                if isinstance(node.get("city"), dict): loc = node["city"].get("name", "")
                out.append({"id": str(jid), "title": title, "company": comp,
                            "location": loc, "url": node.get("url") or ""})
            for v in node.values():
                if isinstance(v, (dict, list)): self._dig_jobs(v, out)
        return out

    @api.model
    def cron_poll_all(self):
        for s in self.search([("active","=",True)]):
            s._poll()

    def action_view_items(self):
        return {"type":"ir.actions.act_window","name":"Items",
                "res_model":"cb.feed.item","view_mode":"list,form",
                "domain":[("source_id","=",self.id)]}


class CbFeedItem(models.Model):
    _name = "cb.feed.item"
    _description = "Feed Item"
    _order = "published desc, id desc"
    _rec_name = "title"

    source_id = fields.Many2one("cb.feed.source", required=True, ondelete="cascade", index=True)
    source_type = fields.Selection(related="source_id.source_type", store=True)
    guid = fields.Char(index=True)
    title = fields.Char()
    author = fields.Char()
    url = fields.Char()
    summary = fields.Text()
    published = fields.Datetime(index=True)

    _unique_item = models.Constraint("UNIQUE(source_id, guid)", "Duplicate feed item.")
