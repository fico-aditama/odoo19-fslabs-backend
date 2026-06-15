import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage } from "telegram/events/index.js";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";
import { BaseAdapter } from "../core/BaseAdapter.js";

const API_ID   = parseInt(process.env.TG_API_ID || "0", 10);
const API_HASH = process.env.TG_API_HASH || "";
const FETCH_HISTORY = process.env.TG_FETCH_HISTORY === "true";
const HISTORY_LIMIT = parseInt(process.env.TG_HISTORY_LIMIT || "200", 10);
const HISTORY_DELAY = parseInt(process.env.TG_HISTORY_DELAY_MS || "2000", 10);

export class TelegramAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.phone = cfg.phone;
        this.dir = join(this.sessionsDir, "telegram");
        if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true });
        this.sessionFile = join(this.dir, `${this.token}.session`);
        this.client = null;
        this._codeResolver = null;
        this._passwordResolver = null;
    }

    _loadSession() {
        try { return existsSync(this.sessionFile) ? readFileSync(this.sessionFile, "utf8").trim() : ""; }
        catch { return ""; }
    }
    _saveSession(s) { try { writeFileSync(this.sessionFile, s, "utf8"); } catch {} }

    async submitCode(code)     { if (this._codeResolver) { this._codeResolver(code); this._codeResolver = null; return true; } return false; }
    async submitPassword(pw)   { if (this._passwordResolver) { this._passwordResolver(pw); this._passwordResolver = null; return true; } return false; }

    async start() {
        this._stopped = false;
        await this._setStatus("connecting");
        this._log("Starting Telegram…");
        const session = new StringSession(this._loadSession());
        this.client = new TelegramClient(session, API_ID, API_HASH, { connectionRetries: 5 });

        try {
            await this.client.start({
                phoneNumber: async () => this.phone,
                phoneCode: async () => {
                    await this._setStatus("waiting_code");
                    this._log("Menunggu OTP…");
                    return await new Promise((r) => { this._codeResolver = r; });
                },
                password: async () => {
                    await this._setStatus("waiting_password");
                    this._log("Menunggu 2FA…");
                    return await new Promise((r) => { this._passwordResolver = r; });
                },
                onError: (e) => this._log(`Login err: ${e.message}`),
            });
            this._saveSession(this.client.session.save());
            const me = await this.client.getMe();
            await this._setStatus("connected", { username: me.username || me.firstName || this.phone });
            this._log(`✅ Connected as @${me.username || me.firstName}`);

            await this._syncDialogs();
            if (FETCH_HISTORY) await this._fetchHistory();
            this._registerHandlers();
        } catch (e) {
            await this._setStatus("auth_failure");
            this._log(`Auth gagal: ${e.message}`);
        }
    }

    _chatMeta(entity, fallbackId) {
        let type = "dm", name = "";
        if (entity?.className === "Channel") {
            type = entity.megagroup ? "group" : "channel"; name = entity.title || "";
        } else if (entity?.className === "Chat") {
            type = "group"; name = entity.title || "";
        } else {
            type = "dm";
            name = [entity?.firstName, entity?.lastName].filter(Boolean).join(" ")
                   || entity?.username || String(fallbackId);
        }
        return { type, name };
    }

    // FIX: iterDialogs ambil SEMUA, bukan limit 200
    async _syncDialogs() {
        try {
            this._log("Ambil SEMUA dialog…");
            const chats = [];
            for await (const dialog of this.client.iterDialogs({})) {
                const e = dialog.entity;
                if (!e) continue;
                const { type, name } = this._chatMeta(e, dialog.id);
                chats.push({ chat_jid: String(dialog.id), name, chat_type: type });
            }
            await this.syncChats(chats);
            this._log(`✅ Sync ${chats.length} chats`);
        } catch (e) { this._log(`Sync error: ${e.message}`); }
    }

    async _fetchHistory() {
        this._log(`Fetch history (limit/chat: ${HISTORY_LIMIT || "SEMUA"})…`);
        let total = 0;
        for await (const dialog of this.client.iterDialogs({})) {
            if (this._stopped) break;
            const e = dialog.entity;
            if (!e) continue;
            const chatId = String(dialog.id);
            const { type, name } = this._chatMeta(e, dialog.id);
            try {
                const opts = HISTORY_LIMIT > 0 ? { limit: HISTORY_LIMIT } : {};
                for await (const msg of this.client.iterMessages(e, opts)) {
                    if (this._stopped) break;
                    if (!msg.message && !msg.media) continue;
                    this._enqueue(await this._build(msg, chatId, type, name));
                    total++;
                }
            } catch (err) {
                if (/FLOOD_WAIT/.test(err.message) || err.seconds) {
                    const w = (err.seconds || 30) * 1000;
                    this._log(`FloodWait ${err.seconds}s…`);
                    await new Promise((r) => setTimeout(r, w));
                } else { this._log(`Skip ${chatId}: ${err.message}`); }
            }
            await new Promise((r) => setTimeout(r, HISTORY_DELAY));
        }
        await this._flushBatch();
        this._log(`✅ History selesai: ${total} pesan`);
    }

    _registerHandlers() {
        this.client.addEventHandler(async (event) => {
            try {
                const msg = event.message;
                if (!msg) return;
                const chat = await msg.getChat();
                const chatId = String(msg.chatId);
                const { type, name } = this._chatMeta(chat, chatId);
                this._enqueue(await this._build(msg, chatId, type, name));
            } catch (e) { this._log(`Handler err: ${e.message}`); }
        }, new NewMessage({}));
    }

    async _build(msg, chatId, chatType, chatName) {
        let senderName = "";
        try {
            const s = await msg.getSender();
            if (s) senderName = [s.firstName, s.lastName].filter(Boolean).join(" ") || s.username || String(s.id);
        } catch {}
        let type = "text";
        if (msg.photo) type = "image";
        else if (msg.video) type = "video";
        else if (msg.audio || msg.voice) type = "audio";
        else if (msg.document) type = "document";
        else if (msg.sticker) type = "sticker";
        else if (msg.geo) type = "location";

        return {
            chat_jid: chatId, chat_name: chatName, chat_type: chatType,
            ext_message_id: `${chatId}_${msg.id}`,
            sender_jid: msg.senderId ? String(msg.senderId) : "",
            sender_name: senderName,
            is_from_me: msg.out || false,
            message_type: type,
            message_text: msg.message || "",
            message_time: this.nowStr(new Date((msg.date || 0) * 1000)),
        };
    }

    async stop() {
        this._stopped = true;
        await this._flushBatch();
        try { await this.client?.disconnect(); } catch {}
        this.status = "stopped";
        this._log("Stopped.");
    }

    async sendMessage({ to, text }) {
        if (!this.client) throw new Error("not_connected");
        await this.client.sendMessage(to, { message: text || "" });
        return { ok: true };
    }
}
