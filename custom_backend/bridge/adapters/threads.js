import axios from "axios";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { BaseAdapter } from "../core/BaseAdapter.js";

const API_POLL    = Math.max(60, parseInt(process.env.TH_API_POLL_SEC || "120", 10));
const SCRAPE_POLL = Math.max(60, parseInt(process.env.TH_SCRAPE_POLL_SEC || "180", 10));
const THREADS_API = "https://graph.threads.net/v1.0";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36";

export class ThreadsAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.mode = cfg.mode || "api";
        this.accessToken = cfg.access_token || "";
        this.scrapeUser = cfg.scrape_user || "";
        this.dir = join(this.sessionsDir, "threads");
        if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true });
        this.seenFile = join(this.dir, `${this.token}.seen.json`);
        this._seen = new Set();
        this._pollTimer = null;
        this.userId = "";
        this._loadSeen();
    }

    _loadSeen() { try { if (existsSync(this.seenFile)) this._seen = new Set(JSON.parse(readFileSync(this.seenFile, "utf8")).slice(-3000)); } catch {} }
    _saveSeen() { try { writeFileSync(this.seenFile, JSON.stringify([...this._seen].slice(-3000))); } catch {} }

    async start() {
        this._stopped = false;
        await this._setStatus("connecting");
        this._log(`Starting Threads (${this.mode})…`);
        if (this.mode === "api") {
            try {
                const r = await axios.get(`${THREADS_API}/me`, { params: { fields: "id,username", access_token: this.accessToken } });
                this.userId = r.data.id;
                this._log(`✅ API @${r.data.username}`);
            } catch (e) {
                await this._setStatus("auth_failure");
                this._log(`API gagal: ${e.response?.data?.error?.message || e.message}`);
                return;
            }
        }
        await this._setStatus("connected");
        this._startPolling();
    }

    _startPolling() {
        clearTimeout(this._pollTimer);
        const interval = (this.mode === "api" ? API_POLL : SCRAPE_POLL) * 1000;
        const loop = async () => {
            if (this._stopped) return;
            try { this.mode === "api" ? await this._pollApi() : await this._pollScrape(); }
            catch (e) { this._log(`Poll err: ${e.message}`); }
            this._pollTimer = setTimeout(loop, interval);
        };
        this._pollTimer = setTimeout(loop, 2000);
        this._log(`Polling tiap ${interval/1000}s`);
    }

    async _pollApi() {
        const res = await axios.get(`${THREADS_API}/me/threads`, {
            params: { fields: "id,media_type,text,permalink,timestamp,username", limit: 25, access_token: this.accessToken },
        });
        for (const p of (res.data?.data || [])) {
            const id = `post_${p.id}`;
            if (this._seen.has(id)) continue;
            this._seen.add(id);
            this._enqueue({
                chat_jid: `threads_${p.username || "me"}`,
                chat_name: `Threads @${p.username || "me"}`,
                chat_type: "channel",
                ext_message_id: p.id,
                sender_name: p.username || "me",
                is_from_me: true,
                message_type: (p.media_type || "text").toLowerCase() === "text" ? "text" : "image",
                message_text: p.text || "",
                message_time: this.nowStr(p.timestamp ? new Date(p.timestamp) : new Date()),
                permalink: p.permalink || "",
            });
        }
        this._saveSeen();
    }

    async _pollScrape() {
        if (!this.scrapeUser) { this._log("scrape_user kosong"); return; }
        try {
            const res = await axios.get(`https://www.threads.net/@${this.scrapeUser}`, {
                headers: { "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9" }, timeout: 20000,
            });
            const matches = [...res.data.matchAll(/<script type="application\/json"[^>]*>(.*?)<\/script>/gs)];
            let n = 0;
            for (const m of matches) {
                try { n += this._extract(JSON.parse(m[1])); } catch {}
            }
            if (n) this._log(`Scrape @${this.scrapeUser}: ${n} baru`);
            this._saveSeen();
        } catch (e) { this._log(`Scrape err: ${e.response?.status || e.message}`); }
    }

    _extract(node, count = 0) {
        if (!node || typeof node !== "object") return count;
        if (Array.isArray(node)) { for (const x of node) count = this._extract(x, count); return count; }
        const text = node.caption?.text || node.text;
        const pk = node.pk || node.id || node.code;
        if (text && pk) {
            const id = `scrape_${pk}`;
            if (!this._seen.has(id)) {
                this._seen.add(id);
                this._enqueue({
                    chat_jid: `threads_${this.scrapeUser}`,
                    chat_name: `Threads @${this.scrapeUser}`,
                    chat_type: "channel",
                    ext_message_id: String(pk),
                    sender_name: node.user?.username || this.scrapeUser,
                    is_from_me: false,
                    message_type: "text", message_text: text,
                    message_time: this.nowStr(node.taken_at ? new Date(node.taken_at * 1000) : new Date()),
                    permalink: node.code ? `https://www.threads.net/t/${node.code}` : "",
                });
                count++;
            }
        }
        for (const k in node) if (typeof node[k] === "object") count = this._extract(node[k], count);
        return count;
    }

    async stop() {
        this._stopped = true;
        clearTimeout(this._pollTimer);
        await this._flushBatch();
        this._saveSeen();
        this.status = "stopped";
        this._log("Stopped.");
    }
}
