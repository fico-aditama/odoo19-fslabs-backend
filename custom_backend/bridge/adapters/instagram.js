import pkg from "instagram-private-api";
const { IgApiClient, IgCheckpointError, IgLoginRequiredError } = pkg;
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { BaseAdapter } from "../core/BaseAdapter.js";

const POLL_SEC = Math.max(30, parseInt(process.env.IG_POLL_SEC || "45", 10));

export class InstagramAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.username = cfg.username;
        this.password = cfg.password;
        this.dir = join(this.sessionsDir, "instagram");
        if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true });
        this.sessionFile = join(this.dir, `${this.token}.json`);
        this.ig = null;
        this._pollTimer = null;
        this._seen = new Set();
        this._challengeResolver = null;
    }

    async submitCode(code) { if (this._challengeResolver) { this._challengeResolver(code); this._challengeResolver = null; return true; } return false; }

    async _saveSession() {
        try { const s = await this.ig.state.serialize(); delete s.constants; writeFileSync(this.sessionFile, JSON.stringify(s)); } catch {}
    }
    async _loadSession() {
        if (!existsSync(this.sessionFile)) return false;
        try { await this.ig.state.deserialize(JSON.parse(readFileSync(this.sessionFile, "utf8"))); return true; } catch { return false; }
    }

    async start() {
        this._stopped = false;
        await this._setStatus("connecting");
        this._log("Starting Instagram…");
        this.ig = new IgApiClient();
        this.ig.state.generateDevice(this.username);
        const has = await this._loadSession();
        try {
            if (has) {
                try { await this.ig.account.currentUser(); this._log("Session lama valid."); }
                catch { await this._doLogin(); }
            } else { await this._doLogin(); }
            await this._saveSession();
            await this._setStatus("connected", { username: this.username });
            this._log(`✅ @${this.username}`);
            await this._syncThreads();
            this._startPolling();
        } catch (e) {
            if (e instanceof IgCheckpointError) await this._handleCheckpoint();
            else { await this._setStatus("auth_failure"); this._log(`Login gagal: ${e.message}`); }
        }
    }

    async _doLogin() {
        await this.ig.simulate.preLoginFlow();
        await this.ig.account.login(this.username, this.password);
        await this.ig.simulate.postLoginFlow();
    }

    async _handleCheckpoint() {
        await this._setStatus("waiting_challenge");
        this._log("⚠️ Checkpoint — minta verifikasi");
        try {
            await this.ig.challenge.auto(true);
            const code = await new Promise((r) => { this._challengeResolver = r; });
            await this.ig.challenge.sendSecurityCode(code);
            await this._saveSession();
            await this._setStatus("connected", { username: this.username });
            this._log(`✅ Challenge lolos`);
            await this._syncThreads();
            this._startPolling();
        } catch (e) { await this._setStatus("auth_failure"); this._log(`Challenge gagal: ${e.message}`); }
    }

    _startPolling() {
        clearTimeout(this._pollTimer);
        const loop = async () => {
            if (this._stopped || this.status !== "connected") return;
            try { await this._poll(); }
            catch (e) {
                this._log(`Poll err: ${e.message}`);
                if (e instanceof IgLoginRequiredError) { await this._setStatus("auth_failure"); return; }
            }
            this._pollTimer = setTimeout(loop, POLL_SEC * 1000);
        };
        this._pollTimer = setTimeout(loop, POLL_SEC * 1000);
        this._log(`Polling tiap ${POLL_SEC}s`);
    }

    async _poll() {
        const inbox = await this.ig.feed.directInbox().items();
        for (const thread of inbox) {
            const tid = thread.thread_id;
            const name = thread.thread_title || thread.users?.map((u) => u.username).join(", ") || tid;
            const isGroup = (thread.users?.length || 0) > 1;
            for (const item of (thread.items || [])) {
                const mid = `${tid}_${item.item_id}`;
                if (this._seen.has(mid)) continue;
                this._seen.add(mid);
                if (this._seen.size > 5000) this._seen = new Set([...this._seen].slice(-2000));
                this._enqueue(this._build(item, thread, tid, name, isGroup));
            }
        }
    }

    _build(item, thread, tid, name, isGroup) {
        const senderId = String(item.user_id || "");
        const sender = thread.users?.find((u) => String(u.pk) === senderId);
        let type = "text", text = "";
        if (item.item_type === "text") text = item.text || "";
        else if (item.item_type === "media") type = item.media?.media_type === 2 ? "video" : "image";
        else if (item.item_type === "voice_media") type = "audio";
        else if (item.item_type === "animated_media") type = "sticker";
        else if (item.item_type === "link") text = item.link?.text || "";
        else { type = "other"; text = `[${item.item_type}]`; }

        return {
            chat_jid: tid, chat_name: name, chat_type: isGroup ? "group" : "dm",
            ext_message_id: `${tid}_${item.item_id}`,
            sender_jid: senderId,
            sender_name: sender?.username || senderId,
            is_from_me: String(item.user_id) === String(this.ig.state.cookieUserId),
            message_type: type, message_text: text,
            message_time: this.nowStr(new Date(Number(item.timestamp) / 1000)),
        };
    }

    async _syncThreads() {
        try {
            const inbox = await this.ig.feed.directInbox().items();
            const chats = inbox.map((t) => ({
                chat_jid: t.thread_id,
                name: t.thread_title || t.users?.map((u) => u.username).join(", ") || t.thread_id,
                chat_type: (t.users?.length || 0) > 1 ? "group" : "dm",
            }));
            await this.syncChats(chats);
            this._log(`Sync ${chats.length} threads`);
        } catch (e) { this._log(`Sync err: ${e.message}`); }
    }

    async stop() {
        this._stopped = true;
        clearTimeout(this._pollTimer);
        await this._flushBatch();
        this.status = "stopped";
        this._log("Stopped.");
    }

    async sendMessage({ to, text }) {
        const thread = this.ig.entity.directThread(to);
        await thread.broadcastText(text || "");
        return { ok: true };
    }
}
