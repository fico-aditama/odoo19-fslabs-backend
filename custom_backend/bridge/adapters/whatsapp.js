import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import qrcodeTerminal from "qrcode-terminal";
import QRCode from "qrcode";
import { join } from "path";
import { existsSync, mkdirSync } from "fs";
import { BaseAdapter } from "../core/BaseAdapter.js";

export class WhatsAppAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.dir = join(this.sessionsDir, "whatsapp", this.token);
        if (!existsSync(this.dir)) mkdirSync(this.dir, { recursive: true });
        this.sock = null;
        this.groupNames = {};   // jid → subject (biar pesan grup dpt nama benar)
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
                qrcodeTerminal.generate(qr, { small: true });  // backup di terminal
                // generate PNG base64 untuk Odoo
                let qrImage = "";
                try {
                    const dataUrl = await QRCode.toDataURL(qr, { width: 256, margin: 1 });
                    qrImage = dataUrl.split(",")[1];   // buang prefix data:image/png;base64,
                } catch (e) { this._log(`QR png gagal: ${e.message}`); }
                await this._setStatus("waiting_qr", { qr, qr_image: qrImage });
                this._log("QR ready — scan via Odoo atau terminal.");
            }
            if (connection === "open") {
                await this._setStatus("connected", { username: this.sock.user?.id?.split(":")[0] });
                this._log(`✅ Connected: ${this.sock.user?.id}`);
                await this._syncGroups();
                await this._syncContacts();
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

        // ── CONTACT SYNC (fix: sebelumnya tidak pernah dipanggil) ──
        this.sock.ev.on("contacts.upsert", (contacts) => this._pushContacts(contacts));
        this.sock.ev.on("contacts.update", (contacts) => this._pushContacts(contacts));

        this.sock.ev.on("messages.upsert", async ({ messages, type }) => {
            if (type !== "notify") return;
            for (const m of messages) {
                const p = this._build(m);
                if (p) this._enqueue(p);
            }
        });

        // REACTION (event terpisah)
        this.sock.ev.on("messages.reaction", async (reactions) => {
            const items = (reactions || []).map((r) => ({
                ext_message_id: r.key?.id,
                reactions: r.reaction?.text || "",
            })).filter((x) => x.ext_message_id);
            if (items.length) {
                await this.pushToOdoo("/cb/reaction", { session_token: this.token, reactions: items });
            }
        });
    }

    _pushContacts(contacts) {
        const list = (contacts || [])
            .filter((c) => c.id)
            .map((c) => ({ jid: c.id, name: c.name || c.notify || c.verifiedName || "" }));
        if (list.length) this.syncContacts(list);
    }

    async _syncGroups() {
        try {
            const groups = await this.sock.groupFetchAllParticipating();
            const chats = Object.values(groups).map((g) => {
                this.groupNames[g.id] = g.subject || g.id;   // simpan nama grup
                return { chat_jid: g.id, name: g.subject || g.id, chat_type: "group" };
            });
            await this.syncChats(chats);
            this._log(`Sync ${chats.length} groups`);
        } catch (e) { this._log(`Sync groups error: ${e.message}`); }
    }

    async _syncContacts() {
        // ambil contact dari store kalau ada
        try {
            const store = this.sock.store?.contacts || {};
            const list = Object.values(store)
                .filter((c) => c.id)
                .map((c) => ({ jid: c.id, name: c.name || c.notify || c.verifiedName || "" }));
            if (list.length) {
                await this.syncContacts(list);
                this._log(`Sync ${list.length} contacts`);
            }
        } catch (e) { this._log(`Sync contacts error: ${e.message}`); }
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

        // detail: quoted/reply, forward, mentions
        const ctx = m.message.extendedTextMessage?.contextInfo
                 || m.message.imageMessage?.contextInfo
                 || m.message.videoMessage?.contextInfo;
        const quoted = ctx?.quotedMessage?.conversation
            || ctx?.quotedMessage?.extendedTextMessage?.text || "";
        const replyToId = ctx?.stanzaId || null;
        const isForwarded = !!ctx?.isForwarded || (ctx?.forwardingScore || 0) > 0;
        const fwdScore = ctx?.forwardingScore || 0;
        const mentioned = (ctx?.mentionedJid || []).join(", ");

        // FIX nama grup: untuk grup pakai subject dari map, JANGAN pushName
        const senderJid = isGroup ? (m.key.participant || "") : jid;
        const chatName = isGroup
            ? (this.groupNames[jid] || jid)     // nama grup, bukan pengirim
            : (m.pushName || jid);              // DM: nama kontak

        return {
            chat_jid: jid,
            chat_name: chatName,
            chat_type: isGroup ? "group" : "dm",
            ext_message_id: m.key.id,
            sender_jid: senderJid,
            sender_name: m.pushName || "",       // nama pengirim tetap di sender_name
            is_from_me: m.key.fromMe || false,
            message_type: type,
            message_text: content,
            message_time: this.nowStr(new Date((m.messageTimestamp || Date.now()/1000) * 1000)),
            quoted_text_preview: quoted ? quoted.slice(0, 120) : null,
            reply_to_ext_id: replyToId,
            is_forwarded: isForwarded,
            forward_score: fwdScore,
            mentions: mentioned || null,
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
