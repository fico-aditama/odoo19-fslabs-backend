import pkg from "discord.js-selfbot-v13";
const { Client } = pkg;
import { BaseAdapter } from "../core/BaseAdapter.js";

export class DiscordAdapter extends BaseAdapter {
    constructor(cfg, deps) {
        super(cfg, deps);
        this.discordToken = cfg.discord_token;
        this.client = null;
        this._reconnect = null;
    }

    async start() {
        this._stopped = false;
        await this._setStatus("connecting");
        this._log("Starting Discord…");
        await this._connect();
    }

    async _connect() {
        this.client = new Client({ checkUpdate: false });

        this.client.on("ready", async () => {
            const u = this.client.user;
            await this._setStatus("connected", { username: `${u.username}#${u.discriminator}` });
            this._log(`✅ ${u.username}#${u.discriminator}`);
            await this._syncGuilds();
        });
        this.client.on("messageCreate", (m) => this._enqueue(this._build(m)));
        this.client.on("messageUpdate", (_, m) => { if (m?.content) this._enqueue(this._build(m, true)); });
        this.client.on("messageReactionAdd", async (reaction) => {
            try {
                const msg = reaction.message;
                await this.pushToOdoo("/cb/reaction", {
                    session_token: this.token,
                    reactions: [{
                        ext_message_id: `${msg.channel?.id}_${msg.id}`,
                        reactions: reaction.emoji?.name || "",
                    }],
                });
            } catch (e) { this._log(`reaction err: ${e.message}`); }
        });
        this.client.on("error", (e) => this._log(`err: ${e.message}`));
        this.client.on("disconnect", () => {
            if (!this._stopped) { this._log("Reconnect 5s…"); this._reconnect = setTimeout(() => this._connect(), 5000); }
        });

        try { await this.client.login(this.discordToken); }
        catch (e) { await this._setStatus("auth_failure"); this._log(`Login gagal: ${e.message}`); }
    }

    async _syncGuilds() {
        try {
            const chats = [];
            for (const g of this.client.guilds.cache.values()) {
                for (const c of g.channels.cache.values()) {
                    if (c.type === "GUILD_TEXT" || c.type === 0)
                        chats.push({ chat_jid: c.id, name: `${g.name} / #${c.name}`, chat_type: "channel" });
                }
            }
            await this.syncChats(chats);
            this._log(`Sync ${chats.length} channels`);
        } catch (e) { this._log(`Sync err: ${e.message}`); }
    }

    _build(msg, edited = false) {
        if (msg.author?.bot && msg.author?.id !== this.client?.user?.id) return null;
        const isGuild = !!msg.guild;
        const chatId = msg.channel?.id || "";
        let type = "text";
        if (msg.attachments?.size > 0) {
            const mime = msg.attachments.first()?.contentType || "";
            type = mime.startsWith("image/") ? "image" : mime.startsWith("video/") ? "video"
                 : mime.startsWith("audio/") ? "audio" : "document";
        } else if (msg.stickers?.size > 0) type = "sticker";

        const att = msg.attachments?.first();
        const replyToId = msg.reference?.messageId ? `${chatId}_${msg.reference.messageId}` : null;
        let mentions = "";
        try { mentions = msg.mentions?.users?.map((u) => u.username).join(", ") || ""; } catch {}
        return {
            chat_jid: chatId,
            chat_name: isGuild ? `${msg.guild?.name} / #${msg.channel?.name}` : `DM:${msg.author?.username}`,
            chat_type: isGuild ? "channel" : "dm",
            ext_message_id: `${chatId}_${msg.id}`,
            sender_jid: msg.author?.id || "",
            sender_name: msg.author?.username || "",
            is_from_me: msg.author?.id === this.client?.user?.id,
            is_edited: edited,
            message_type: type,
            message_text: msg.content || "",
            message_time: this.nowStr(msg.createdAt || new Date()),
            attachment_url: att?.url || null,
            attachment_mime: att?.contentType || null,
            attachment_filename: att?.name || null,
            reply_to_ext_id: replyToId,
            mentions: mentions || null,
        };
    }

    async stop() {
        this._stopped = true;
        clearTimeout(this._reconnect);
        await this._flushBatch();
        try { this.client?.destroy(); } catch {}
        this.status = "stopped";
        this._log("Stopped.");
    }

    async sendMessage({ to, text }) {
        const ch = await this.client.channels.fetch(to);
        await ch.send(text || "");
        return { ok: true };
    }
}
