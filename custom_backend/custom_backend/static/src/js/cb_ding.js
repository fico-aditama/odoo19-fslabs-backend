/** @odoo-module **/
import { registry } from "@web/core/registry";

/**
 * Service: dengar bus channel 'cb_ding', bunyikan 'ding' (Web Audio, tanpa file)
 * + tampilkan toast notifikasi pesan masuk.
 */
const cbDingService = {
    dependencies: ["bus_service", "notification"],
    start(env, { bus_service, notification }) {
        function ding() {
            try {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                if (!Ctx) return;
                const ctx = new Ctx();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.type = "sine";
                osc.frequency.setValueAtTime(880, ctx.currentTime);
                osc.frequency.setValueAtTime(1320, ctx.currentTime + 0.08);
                gain.gain.setValueAtTime(0.15, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
                osc.start();
                osc.stop(ctx.currentTime + 0.25);
            } catch (e) { /* ignore */ }
        }

        bus_service.addChannel("cb_ding");
        const ICON = {
            whatsapp: "🟢 WhatsApp", telegram: "🔵 Telegram", discord: "🟣 Discord",
            instagram: "🟠 Instagram", threads: "⚫ Threads", email: "✉️ Email",
        };
        bus_service.subscribe("cb_ding", (payload) => {
            ding();
            const plat = ICON[payload.platform] || payload.platform || "Pesan";
            notification.add(
                `${payload.sender || payload.chat || ""}: ${payload.preview || ""}`,
                { title: `${plat} — ${payload.chat || ""}`, type: "info", sticky: false }
            );
        });
        bus_service.start();
    },
};

registry.category("services").add("cb_ding", cbDingService);
