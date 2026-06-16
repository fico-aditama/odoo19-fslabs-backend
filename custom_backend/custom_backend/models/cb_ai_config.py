"""
AI config + provider router. Dipakai untuk summarize & auto-reply.
Support: OpenAI, Gemini, dan Custom endpoint (mis. FastAPI/LangChain sendiri).
"""
import json
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError


class CbAiConfig(models.Model):
    _name = "cb.ai.config"
    _description = "AI Configuration"

    name = fields.Char(default="AI Config", readonly=True)
    enabled = fields.Boolean(string="Enabled", default=True)
    provider = fields.Selection([
        ("openai", "OpenAI"),
        ("gemini", "Google Gemini"),
        ("custom", "Custom Endpoint (FastAPI dll)"),
    ], default="openai", required=True, string="Provider")

    api_key = fields.Char(string="API Key", help="OpenAI/Gemini API key")
    model = fields.Char(string="Model", default="gpt-4o-mini",
                        help="bisa ketik manual atau pilih dari preset")
    model_preset = fields.Selection([
        ("gpt-4o-mini", "OpenAI · gpt-4o-mini (murah)"),
        ("gpt-4o", "OpenAI · gpt-4o"),
        ("gpt-4.1-mini", "OpenAI · gpt-4.1-mini"),
        ("gpt-4.1", "OpenAI · gpt-4.1"),
        ("gemini-2.0-flash", "Gemini · 2.0-flash"),
        ("gemini-2.5-flash", "Gemini · 2.5-flash"),
        ("gemini-2.5-pro", "Gemini · 2.5-pro"),
    ], string="Model Preset", help="Pilih → otomatis isi field Model")

    @api.onchange("model_preset")
    def _onchange_preset(self):
        if self.model_preset:
            self.model = self.model_preset
    base_url = fields.Char(string="Custom Base URL",
                           help="Untuk provider custom: URL endpoint yang terima {prompt} dan balas {text}")
    temperature = fields.Float(string="Temperature", default=0.3)
    max_tokens = fields.Integer(string="Max Tokens", default=1024)

    summary_prompt = fields.Text(
        string="Summary System Prompt",
        default=("You are an assistant that summarizes chat conversations. "
                 "Summarize only the IMPORTANT items per chat: decisions, deadlines, "
                 "requests, unanswered questions, and key information. "
                 "Ignore small talk. Keep it concise, use bullet points per chat, in English."))

    def _get(self):
        cfg = self.sudo().search([], limit=1)
        if not cfg:
            cfg = self.sudo().create({})
        return cfg

    def action_test(self):
        self.ensure_one()
        try:
            out = self._call_ai("Balas dengan satu kata: OK")
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": "AI Test", "message": f"Respons: {out[:200]}",
                           "type": "success", "sticky": False},
            }
        except Exception as e:
            raise UserError(f"AI test gagal: {e}")

    # ── PROVIDER ROUTER (dipakai ulang oleh summarize & auto-reply) ──
    def _call_ai(self, user_prompt, system_prompt=None):
        self.ensure_one()
        if not self.enabled:
            raise UserError("AI sedang disabled di config.")
        if self.provider == "openai":
            return self._call_openai(user_prompt, system_prompt)
        if self.provider == "gemini":
            return self._call_gemini(user_prompt, system_prompt)
        if self.provider == "custom":
            return self._call_custom(user_prompt, system_prompt)
        raise UserError("Provider tidak dikenal.")

    def _call_openai(self, user_prompt, system_prompt=None):
        if not self.api_key:
            raise UserError("OpenAI API key kosong.")
        model = (self.model or "gpt-4o-mini").strip()
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": user_prompt})
        payload = {"model": model, "messages": msgs}
        # model reasoning baru (o1/o3/o4/gpt-5) pakai max_completion_tokens & tolak temperature
        is_reasoning = any(model.startswith(p) for p in ("o1", "o3", "o4", "gpt-5"))
        if is_reasoning:
            payload["max_completion_tokens"] = self.max_tokens or 1024
        else:
            payload["max_tokens"] = self.max_tokens or 1024
            payload["temperature"] = self.temperature
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=120)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                err = resp.text[:300]
            raise UserError(f"OpenAI {resp.status_code}: {err}")
        return resp.json()["choices"][0]["message"]["content"].strip()

    def _call_gemini(self, user_prompt, system_prompt=None):
        if not self.api_key:
            raise UserError("Gemini API key kosong.")
        model = self.model or "gemini-2.0-flash"
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={self.api_key}")
        parts = []
        if system_prompt:
            parts.append({"text": system_prompt + "\n\n"})
        parts.append({"text": user_prompt})
        resp = requests.post(url, json={
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": self.temperature,
                                 "maxOutputTokens": self.max_tokens},
        }, timeout=120)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                err = resp.text[:300]
            raise UserError(f"Gemini {resp.status_code}: {err}")
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            raise UserError(f"Gemini respons tak terduga: {str(data)[:300]}")

    def _call_custom(self, user_prompt, system_prompt=None):
        if not self.base_url:
            raise UserError("Custom base_url kosong.")
        resp = requests.post(self.base_url.rstrip("/"), json={
            "prompt": user_prompt, "system": system_prompt or "",
            "model": self.model or "", "temperature": self.temperature,
        }, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        # terima beberapa kemungkinan bentuk respons
        return (data.get("text") or data.get("response")
                or data.get("content") or json.dumps(data))[:8000]
