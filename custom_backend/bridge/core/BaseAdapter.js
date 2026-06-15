/**
 * BaseAdapter — pola umum semua platform adapter.
 * Subclass wajib implement: start(), stop(), dan optionally submitCode/submitPassword/sendMessage.
 * Semua emit pesan via this._enqueue(payload) → push ke Odoo /cb/webhook.
 *
 * Payload unified (field WAJIB konsisten dgn cb.message.record_message):
 *   session_token, chat_jid, chat_name, chat_type (dm|group|channel),
 *   ext_message_id, sender_jid, sender_name, is_from_me, is_edited,
 *   message_type, message_text, message_time (YYYY-MM-DD HH:MM:SS),
 *   attachment_url?, attachment_data?(base64), attachment_mime?, attachment_filename?,
 *   quoted_text_preview?, permalink?
 */

const BATCH_SIZE     = parseInt(process.env.BATCH_SIZE     || "10",   10);
const BATCH_FLUSH_MS = parseInt(process.env.BATCH_FLUSH_MS || "3000", 10);

export class BaseAdapter {
    constructor(cfg, deps) {
        this.token      = cfg.token;          // bridge_token Odoo
        this.platform   = cfg.platform;
        this.cfg        = cfg;
        this.pushToOdoo = deps.pushToOdoo;
        this.onLog      = deps.onLog || (() => {});
        this.sessionsDir= deps.sessionsDir;
        this.status     = "stopped";
        this.batch      = [];
        this.flushTimer = null;
        this._stopped   = false;
    }

    _log(msg) {
        const line = `[${this.platform}:${this.token.slice(0,6)}…] ${msg}`;
        console.log(line);
        this.onLog(this.token, line);
    }

    async _setStatus(status, extra = {}) {
        this.status = status;
        await this.pushToOdoo("/cb/status", {
            session_token: this.token, status, ...extra,
        });
    }

    getStatus() {
        return { token: this.token, platform: this.platform, status: this.status };
    }

    _enqueue(payload) {
        if (!payload) return;
        payload.session_token = this.token;
        this.batch.push(payload);
        if (this.batch.length >= BATCH_SIZE) this._flushBatch();
        else if (!this.flushTimer)
            this.flushTimer = setTimeout(() => this._flushBatch(), BATCH_FLUSH_MS);
    }

    async _flushBatch() {
        clearTimeout(this.flushTimer);
        this.flushTimer = null;
        if (!this.batch.length) return;
        const toSend = this.batch.splice(0);
        const result = await this.pushToOdoo("/cb/webhook", {
            session_token: this.token, messages: toSend,
        });
        if (result) this._log(`✅ Push ${toSend.length} pesan`);
    }

    async syncChats(chats) {
        if (!chats?.length) return;
        const CHUNK = 50;
        for (let i = 0; i < chats.length; i += CHUNK) {
            await this.pushToOdoo("/cb/chats/sync", {
                session_token: this.token, chats: chats.slice(i, i + CHUNK),
            });
        }
    }

    async syncContacts(contacts) {
        if (!contacts?.length) return;
        await this.pushToOdoo("/cb/contacts/sync", {
            session_token: this.token, contacts,
        });
    }

    // override di subclass kalau perlu
    async submitCode()     { return false; }
    async submitPassword() { return false; }
    async sendMessage()    { throw new Error("sendMessage not implemented"); }
    nowStr(d = new Date()) { return d.toISOString().replace("T", " ").slice(0, 19); }
}
