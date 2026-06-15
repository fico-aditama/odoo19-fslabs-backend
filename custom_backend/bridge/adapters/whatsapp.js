import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import qrcode from "qrcode-terminal";
import { join } from "path";
import { existsSync, mkdirSync } from "fs";
import { BaseAdapter } from "../core/BaseAdapter.js";

export class WhatsAppAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.dir = join(this.sessionsDir, "whatsapp", this.token);
        if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true });
        this.sock = null;
    }

    async start() {
        this._stopped = false;
        await this._setStatus("connecting");
        this._log("Starting WhatsApp…");
        const { state, saveCreds } = await useMultiFileAuthState(this.dir);
        const { version } = await fetchLatestBaileysVersion();

        this.sock = makeWASocket({ version, auth: state, printQRInTerminal: false, syncFullHistory: false });

        this.sock.ev.on("creds.update", saveCreds);
        this.sock.ev.on("connection.update", async (u) => {
            const { connection, lastDisconnect, qr } = u;
            if (qr) {
                qrcode.generate(qr, { small: true });
                await this._setStatus("waiting_qr", { qr });
                this._log("QR ready — scan via WhatsApp.");
            }
            if (connection === "open") {
                await this._setStatus("connected", { username: this.sock.user?.id?.split(":")[0] });
                this._log(`✅ Connected: ${this.sock.user?.id}`);
                await this._syncGroups();
            } else if (connection === "close") {
                const code = new Boom(lastDisconnect?.error)?.output?.statusCode;
                if (code === DisconnectReason.loggedOut) {
                    await this._setStatus("disconnected");
                    this._log("Logged out.");
                } else if (!this._stopped) {
                    this._log("Reconnect dalam 5s…");
                    setTimeout(() => this.start(), 5000);
                }
            }
        });

        this.sock.ev.on("messages.upsert", async ({ messages, type }) => {
            if (type !== "notify") return;
            for (const m of messages) {
                const p = this._build(m);
                if (p) this._enqueue(p);
            }
        });
    }

    async _syncGroups() {
        try {
            const groups = await this.sock.groupFetchAllParticipating();
            const chats = Object.values(groups).map((g) => ({
                chat_jid: g.id, name: g.subject || g.id, chat_type: "group",
            }));
            await this.syncChats(chats);
            this._log(`Sync ${chats.length} groups`);
        } catch (e) { this._log(`Sync groups error: ${e.message}`); }
    }

    _build(m) {
        if (!m.message) return null;
        const jid = m.key.remoteJid || "";
        const isGroup = jid.endsWith("@g.us");
        const content = m.message.conversation
            || m.message.extendedTextMessage?.text
            || m.message.imageMessage?.caption
            || m.message.videoMessage?.caption || "";
        let type = "text";
        if (m.message.imageMessage) type = "image";
        else if (m.message.videoMessage) type = "video";
        else if (m.message.audioMessage) type = "audio";
        else if (m.message.documentMessage) type = "document";
        else if (m.message.stickerMessage) type = "sticker";
        else if (m.message.locationMessage) type = "location";

        return {
            chat_jid: jid,
            chat_name: m.pushName || jid,
            chat_type: isGroup ? "group" : "dm",
            ext_message_id: m.key.id,
            sender_jid: m.key.participant || jid,
            sender_name: m.pushName || "",
            is_from_me: m.key.fromMe || false,
            message_type: type,
            message_text: content,
            message_time: this.nowStr(new Date((m.messageTimestamp || Date.now()/1000) * 1000)),
        };
    }

    async stop() {
        this._stopped = true;
        await this._flushBatch();
        try { this.sock?.end(); } catch {}
        this.status = "stopped";
        this._log("Stopped.");
    }

    async sendMessage({ to, text }) {
        if (!this.sock) throw new Error("not_connected");
        await this.sock.sendMessage(to, { text: text || "" });
        return { ok: true };
    }
}
