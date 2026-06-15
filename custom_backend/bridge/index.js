import "dotenv/config";
import { Manager }      from "./core/Manager.js";
import { createServer } from "./core/server.js";

const ODOO_URL     = process.env.ODOO_URL     || "http://localhost:8069";
const SESSIONS_DIR = process.env.SESSIONS_DIR || "./sessions";
const PORT         = parseInt(process.env.BRIDGE_PORT || "3000", 10);

console.log("=".repeat(58));
console.log("  Custom Backend — Unified Multi-Platform Bridge");
console.log("  WhatsApp · Telegram · Discord · Instagram · Threads");
console.log(`  Odoo: ${ODOO_URL}   Port: ${PORT}`);
console.log("=".repeat(58));

const manager = new Manager({ odooUrl: ODOO_URL, sessionsDir: SESSIONS_DIR });
const app = createServer(manager);
app.listen(PORT, () => console.log(`[Bridge] listening on :${PORT}`));
await manager.loadFromOdoo();

async function shutdown() {
    console.log("\n[Bridge] Shutting down…");
    await manager.flushAll();
    process.exit(0);
}
process.on("SIGINT",  shutdown);
process.on("SIGTERM", shutdown);
