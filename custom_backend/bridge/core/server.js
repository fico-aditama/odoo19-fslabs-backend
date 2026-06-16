import express from "express";

export function createServer(manager) {
    const app    = express();
    const SECRET = process.env.BRIDGE_SECRET || "";
    app.use(express.json({ limit: "50mb" }));

    function auth(req, res, next) {
        const s = req.headers["x-bridge-secret"] || req.body?.bridge_secret;
        if (!SECRET || s === SECRET) return next();
        return res.status(401).json({ error: "unauthorized" });
    }

    app.get("/health", (req, res) => res.json({ ok: true, accounts: manager.adapters.size }));
    app.get("/cb/bridge/status", auth, (req, res) => res.json(manager.getStatus()));
    app.post("/cb/bridge/logs", auth, (req, res) => res.json({ logs: manager.getLogs(req.body.token) }));

    app.post("/cb/bridge/connect", auth, async (req, res) => {
        const cfg = req.body;
        if (!cfg.token || !cfg.platform)
            return res.status(400).json({ error: "token & platform required" });
        await manager.addAccount(cfg);
        res.json({ ok: true });
    });

    app.post("/cb/bridge/code", auth, async (req, res) => {
        const ok = await manager.submitCode(req.body.token, String(req.body.code));
        res.json({ ok });
    });
    app.post("/cb/bridge/password", auth, async (req, res) => {
        const ok = await manager.submitPassword(req.body.token, String(req.body.password));
        res.json({ ok });
    });
    app.post("/cb/bridge/disconnect", auth, async (req, res) => {
        await manager.removeAccount(req.body.token);
        res.json({ ok: true });
    });
    app.post("/cb/bridge/send", auth, async (req, res) => {
        try { res.json({ ok: true, ...(await manager.sendMessage(req.body.token, req.body)) }); }
        catch (e) { res.status(400).json({ ok: false, error: e.message }); }
    });

    app.post("/cb/bridge/resync", auth, async (req, res) => {
        try { res.json(await manager.resync(req.body.token)); }
        catch (e) { res.status(400).json({ ok: false, error: e.message }); }
    });

    return app;
}
