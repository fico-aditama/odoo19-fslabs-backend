import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadMediaMessage } from "@whiskeysockets/baileys";
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
                if (this.cfg.track_presence) await this._subscribePresence();
                if (this.cfg.scrape_profiles) this._enrichContacts();  // async, jangan blok
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
                // REVOKE (pesan dihapus untuk semua)
                const proto = m.message?.protocolMessage;
                if (proto && proto.type === 0 && proto.key?.id) {
                    await this.pushToOdoo("/cb/deleted", { session_token: this.token,
                        ext_message_id: proto.key.id });
                    continue;
                }
                const p = await this._build(m);
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

        // READ/UNREAD status (messages.update → status: 2=server,3=delivered,4=read,5=played)
        const STATUS_MAP = { 0: "error", 1: "pending", 2: "sent", 3: "delivered", 4: "read", 5: "played" };
        this.sock.ev.on("messages.update", async (updates) => {
            const items = [];
            for (const u of (updates || [])) {
                const st = u.update?.status;
                if (st !== undefined && u.key?.id) {
                    items.push({ ext_message_id: u.key.id, delivery_status: STATUS_MAP[st] || String(st) });
                }
            }
            if (items.length) await this.pushToOdoo("/cb/receipt", { session_token: this.token, receipts: items });
        });

        // group receipt per participant (siapa yang read)
        this.sock.ev.on("message-receipt.update", async (updates) => {
            const items = [];
            for (const u of (updates || [])) {
                const r = u.receipt;
                if (u.key?.id && r) {
                    const who = r.userJid || u.key.participant || "";
                    let st = "delivered";
                    if (r.readTimestamp) st = "read";
                    else if (r.receiptTimestamp) st = "delivered";
                    items.push({ ext_message_id: u.key.id, delivery_status: st, read_by: who });
                }
            }
            if (items.length) await this.pushToOdoo("/cb/receipt", { session_token: this.token, receipts: items });
        });

        // PRESENCE (online/offline/last seen) — "detective"
        this.sock.ev.on("presence.update", async ({ id, presences }) => {
            try {
                const items = [];
                for (const jid in (presences || {})) {
                    const p = presences[jid];
                    items.push({
                        jid,
                        presence: p.lastKnownPresence || "unavailable",  // available|unavailable|composing|recording
                        last_seen: p.lastSeen ? new Date(p.lastSeen * 1000).toISOString().replace("T"," ").slice(0,19) : null,
                    });
                }
                if (items.length) await this.pushToOdoo("/cb/presence", { session_token: this.token, presences: items });
            } catch (e) { this._log(`presence err: ${e.message}`); }
        });

        // CHAT STATE (archive/pin/mute/unread)
        const pushChatState = async (chats) => {
            const items = [];
            for (const ch of (chats || [])) {
                const v = { chat_jid: ch.id };
                if (ch.archived !== undefined) v.is_archived = !!ch.archived;
                if (ch.pinned !== undefined) v.is_pinned = !!ch.pinned;
                if (ch.muteEndTime !== undefined) v.is_muted = !!ch.muteEndTime;
                if (ch.unreadCount !== undefined) v.unread_count = ch.unreadCount || 0;
                if (Object.keys(v).length > 1) items.push(v);
            }
            if (items.length) await this.pushToOdoo("/cb/chatstate", { session_token: this.token, chats: items });
        };
        this.sock.ev.on("chats.upsert", (chats) => pushChatState(chats));
        this.sock.ev.on("chats.update", (chats) => pushChatState(chats));

        // GROUP participant events (join/leave/promote/demote)
        this.sock.ev.on("group-participants.update", async (ev) => {
            try {
                const names = { add: "bergabung", remove: "keluar", promote: "jadi admin", demote: "dicopot admin" };
                const txt = `👥 ${(ev.participants || []).join(", ")} ${names[ev.action] || ev.action}`;
                this._enqueue({
                    chat_jid: ev.id, chat_name: this.groupNames[ev.id] || ev.id, chat_type: "group",
                    ext_message_id: `sys_${ev.id}_${Date.now()}`,
                    sender_jid: ev.author || "", sender_name: "System",
                    is_from_me: false, message_type: "system", message_text: txt,
                    message_time: this.nowStr(new Date()),
                });
            } catch (e) { this._log(`group ev err: ${e.message}`); }
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

    async _subscribePresence() {
        try {
            const jids = new Set();
            const store = this.sock.store?.contacts || {};
            for (const id in store) if (id.endsWith("@s.whatsapp.net")) jids.add(id);
            for (const id in this.groupNames) jids.add(id);
            let n = 0;
            for (const jid of jids) {
                try { await this.sock.presenceSubscribe(jid); n++; } catch {}
                await new Promise((r) => setTimeout(r, 80));   // jangan flood
            }
            this._log(`Subscribe presence ${n} kontak`);
        } catch (e) { this._log(`subscribe presence err: ${e.message}`); }
    }

    async resync() {
        if (!this.sock) throw new Error("not_connected");
        await this._syncGroups();
        await this._syncContacts();
        if (this.cfg.track_presence) await this._subscribePresence();
        if (this.cfg.scrape_profiles) await this._enrichContacts();
        return { ok: true };
    }

    async _enrichContacts() {
        // scrape profil orang sedetail mungkin (about, foto, business) — throttled
        if (!this.cfg.scrape_profiles) return;
        try {
            const jids = new Set();
            const store = this.sock.store?.contacts || {};
            for (const id in store) if (id.endsWith("@s.whatsapp.net")) jids.add(id);
            let n = 0;
            const profiles = [];
            for (const jid of jids) {
                if (n >= 300) break;  // cap biar nggak kelamaan
                const p = { jid };
                try { const s = await this.sock.fetchStatus(jid); if (s?.status) p.about = s.status; } catch {}
                try { p.avatar_url = await this.sock.profilePictureUrl(jid, "image"); } catch {}
                try {
                    const bp = await this.sock.getBusinessProfile(jid);
                    if (bp) { p.is_business = true; p.business_name = bp.description || bp.business_name || ""; }
                } catch {}
                p.phone_number = jid.split("@")[0];
                if (p.about || p.avatar_url || p.is_business) profiles.push(p);
                n++;
                await new Promise((r) => setTimeout(r, 150));  // throttle
            }
            if (profiles.length) {
                for (let i = 0; i < profiles.length; i += 50)
                    await this.pushToOdoo("/cb/profile", { session_token: this.token, profiles: profiles.slice(i, i+50) });
                this._log(`Enrich ${profiles.length} profil`);
            }
        } catch (e) { this._log(`enrich err: ${e.message}`); }
    }

    async _build(m) {
        if (!m.message) return null;
        const jid = m.key.remoteJid || "";
        const isGroup = jid.endsWith("@g.us");

        // unwrap view-once / ephemeral / edited
        let msg = m.message;
        let isViewOnce = false;
        if (msg.ephemeralMessage) msg = msg.ephemeralMessage.message || msg;
        if (msg.viewOnceMessage)  { msg = msg.viewOnceMessage.message || msg; isViewOnce = true; }
        if (msg.viewOnceMessageV2){ msg = msg.viewOnceMessageV2.message || msg; isViewOnce = true; }
        if (msg.documentWithCaptionMessage) msg = msg.documentWithCaptionMessage.message || msg;
        const edited = msg.editedMessage?.message || msg.protocolMessage?.editedMessage;
        if (edited) msg = edited;

        let content = msg.conversation
            || msg.extendedTextMessage?.text
            || msg.imageMessage?.caption
            || msg.videoMessage?.caption
            || msg.documentMessage?.caption || "";

        let type = "text";
        let extra = {};   // field tambahan
        const im = msg.imageMessage, vm = msg.videoMessage, am = msg.audioMessage,
              dm = msg.documentMessage, sm = msg.stickerMessage, lm = msg.locationMessage,
              llm = msg.liveLocationMessage, cm = msg.contactMessage, pm = msg.pollCreationMessage
                    || msg.pollCreationMessageV2 || msg.pollCreationMessageV3;

        if (im) { type = "image"; extra.media_size = Number(im.fileLength || 0); }
        else if (vm) { type = "video"; extra.media_size = Number(vm.fileLength || 0); extra.media_duration = vm.seconds || 0; }
        else if (am) { type = "audio"; extra.media_size = Number(am.fileLength || 0); extra.media_duration = am.seconds || 0; extra.is_voice_note = !!am.ptt; }
        else if (dm) { type = "document"; extra.media_size = Number(dm.fileLength || 0); }
        else if (sm) { type = "sticker"; extra.media_size = Number(sm.fileLength || 0); }
        else if (lm) { type = "location"; extra.latitude = lm.degreesLatitude; extra.longitude = lm.degreesLongitude;
                       content = `📍 ${lm.name || ""} ${lm.address || ""} (${lm.degreesLatitude},${lm.degreesLongitude})`.trim(); }
        else if (llm){ type = "live_location"; extra.latitude = llm.degreesLatitude; extra.longitude = llm.degreesLongitude;
                       content = `📍live (${llm.degreesLatitude},${llm.degreesLongitude})`; }
        else if (cm) { type = "contact"; content = `👤 ${cm.displayName || ""}\n${cm.vcard || ""}`.slice(0, 1000); }
        else if (pm) { type = "poll"; const opts = (pm.options || []).map((o) => o.optionName).join(" | ");
                       content = `📊 ${pm.name || ""}\nOpsi: ${opts}`; }

        // context: quoted/reply, forward, mentions
        const ctx = msg.extendedTextMessage?.contextInfo || im?.contextInfo || vm?.contextInfo
                 || dm?.contextInfo || am?.contextInfo;
        const qm = ctx?.quotedMessage;
        const quoted = qm?.conversation || qm?.extendedTextMessage?.text
            || qm?.imageMessage?.caption || qm?.videoMessage?.caption || "";
        const replyToId = ctx?.stanzaId || null;
        const isForwarded = !!ctx?.isForwarded || (ctx?.forwardingScore || 0) > 0;
        const fwdScore = ctx?.forwardingScore || 0;
        const mentioned = (ctx?.mentionedJid || []).join(", ");

        const isStory = jid === "status@broadcast";
        const senderJid = (isGroup || isStory) ? (m.key.participant || "") : jid;
        const chatName = isStory ? "WA Status / Stories"
            : (isGroup ? (this.groupNames[jid] || jid) : (m.pushName || jid));

        // download media (kalau diaktifkan & dalam batas ukuran)
        let attData = null, attName = null, attMime = null;
        if (["image","video","audio","document","sticker"].includes(type) && this.cfg.download_media) {
            const cap = (this.cfg.media_max_mb || 16) * 1024 * 1024;
            const size = extra.media_size || 0;
            if (!size || size <= cap) {
                try {
                    const buf = await downloadMediaMessage(m, "buffer", {},
                        { reuploadRequest: this.sock.updateMediaMessage });
                    if (buf && buf.length <= cap) {
                        attData = buf.toString("base64");
                        attMime = im?.mimetype || vm?.mimetype || am?.mimetype
                               || dm?.mimetype || sm?.mimetype || "application/octet-stream";
                        const ext = (attMime.split("/")[1] || "bin").split(";")[0];
                        attName = dm?.fileName || `${type}_${m.key.id}.${ext}`;
                    }
                } catch (e) { this._log(`download media gagal: ${e.message}`); }
            } else {
                this._log(`media skip (>${this.cfg.media_max_mb}MB)`);
            }
        }

        return {
            chat_jid: jid,
            chat_name: chatName,
            chat_type: isStory ? "channel" : (isGroup ? "group" : "dm"),
            is_story: isStory,
            ext_message_id: m.key.id,
            sender_jid: senderJid,
            sender_name: m.pushName || "",
            is_from_me: m.key.fromMe || false,
            is_edited: !!edited,
            is_view_once: isViewOnce,
            message_type: type,
            message_text: content,
            message_time: this.nowStr(new Date((Number(m.messageTimestamp) || Date.now()/1000) * 1000)),
            quoted_text_preview: quoted ? quoted.slice(0, 120) : null,
            reply_to_ext_id: replyToId,
            is_forwarded: isForwarded,
            forward_score: fwdScore,
            mentions: mentioned || null,
            attachment_data: attData,
            attachment_filename: attName,
            attachment_mime: attMime,
            ...extra,
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
