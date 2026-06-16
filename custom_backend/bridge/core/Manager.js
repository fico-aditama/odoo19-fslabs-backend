import axios from "axios";
import { WhatsAppAdapter }  from "../adapters/whatsapp.js";
import { TelegramAdapter }  from "../adapters/telegram.js";
import { DiscordAdapter }   from "../adapters/discord.js";
import { InstagramAdapter } from "../adapters/instagram.js";
import { ThreadsAdapter }   from "../adapters/threads.js";

const ADAPTERS = {
    whatsapp:  WhatsAppAdapter,
    telegram:  TelegramAdapter,
    discord:   DiscordAdapter,
    instagram: InstagramAdapter,
    threads:   ThreadsAdapter,
};

export class Manager {
    constructor({ odooUrl, sessionsDir }) {
        this.odooUrl     = odooUrl.replace(/\/$/, "");
        this.sessionsDir = sessionsDir;
        this.adapters    = new Map();   // token → adapter
        this.logs        = new Map();
        this.odoo = axios.create({
            baseURL: this.odooUrl,
            headers: { "Content-Type": "application/json" },
            timeout: 60_000,
        });
    }

    async _push(path, data) {
        try { const res = await this.odoo.post(path, data); return res.data?.result ?? res.data; }
        catch (err) { console.error(`[Odoo] POST ${path}:`, err.message); return null; }
    }
    _pusher() { return (path, data) => this._push(path, data); }

    _onLog(token, line) {
        if (!this.logs.has(token)) this.logs.set(token, []);
        const arr = this.logs.get(token);
        arr.push(`${new Date().toISOString().slice(11,19)} ${line}`);
        if (arr.length > 200) arr.shift();
    }
    getLogs(token) {
        if (token) return (this.logs.get(token) || []).join("\n");
        let all = [];
        for (const [t, arr] of this.logs) all = all.concat(arr.map((l) => `[${t.slice(0,6)}] ${l}`));
        return all.slice(-300).join("\n");
    }

    async addAccount(cfg) {
        const platform = cfg.platform;
        const Cls = ADAPTERS[platform];
        if (!Cls) { console.error(`Platform tidak dikenal: ${platform}`); return; }

        if (this.adapters.has(cfg.token)) {
            const a = this.adapters.get(cfg.token);
            if (["stopped","disconnected","auth_failure"].includes(a.status)) a.start();
            return;
        }
        const adapter = new Cls(cfg, {
            pushToOdoo: this._pusher(),
            onLog: (t, l) => this._onLog(t, l),
            sessionsDir: this.sessionsDir,
        });
        this.adapters.set(cfg.token, adapter);
        adapter.start();   // jangan await — login bisa lama
    }

    async removeAccount(token) {
        const a = this.adapters.get(token);
        if (!a) return;
        await a.stop();
        this.adapters.delete(token);
    }

    async submitCode(token, code) {
        const a = this.adapters.get(token);
        return a ? await a.submitCode(code) : false;
    }
    async submitPassword(token, pw) {
        const a = this.adapters.get(token);
        return a ? await a.submitPassword(pw) : false;
    }
    async sendMessage(token, opts) {
        const a = this.adapters.get(token);
        if (!a) throw new Error("account_not_found");
        return await a.sendMessage(opts);
    }

    async resync(token) {
        const a = this.adapters.get(token);
        if (!a) throw new Error("account_not_found");
        return await a.resync();
    }

    getStatus() {
        const r = {};
        for (const [t, a] of this.adapters) r[t] = a.getStatus();
        return r;
    }

    async loadFromOdoo() {
        try {
            console.log("[Manager] Ambil accounts dari Odoo…");
            const res = await this.odoo.post("/cb/bridge/accounts", {
                bridge_secret: process.env.BRIDGE_SECRET,
            });
            const data = res.data?.result ?? res.data;
            const accounts = data?.accounts || [];
            console.log(`[Manager] ${accounts.length} account aktif.`);
            for (const acc of accounts) await this.addAccount(acc);
        } catch (err) {
            console.warn("[Manager] Tidak bisa load:", err.message);
        }
    }

    async flushAll() {
        for (const a of this.adapters.values()) await a._flushBatch();
    }
}
