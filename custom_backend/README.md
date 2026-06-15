# Custom Backend — Unified Social Media Recorder (Odoo 19)

SATU module + SATU bridge untuk SEMUA platform: WhatsApp, Telegram, Discord, Instagram, Threads.
Semua pesan masuk ke satu model `cb.message`, dibedakan field `platform` + `message_type`.

## Struktur
```
custom_backend/
├── custom_backend/        ← Odoo module (copy ke addons)
└── bridge/                ← 1 Node.js bridge untuk semua platform
    ├── adapters/          ← whatsapp, telegram, discord, instagram, threads
    ├── core/              ← Manager (orkestrasi) + server + BaseAdapter
    └── sessions/          ← 1 folder, auto subfolder per platform
```

## Setup

### 1. Bridge (1 proses, port 3000)
```bash
cd bridge
cp .env.example .env
# WAJIB isi: ODOO_URL, BRIDGE_SECRET
# Telegram: TG_API_ID, TG_API_HASH (dari my.telegram.org)
npm install
npm start
```

### 2. Odoo
- Copy folder `custom_backend/` ke addons → install module
- Social Recorder → Configuration → Settings → isi Bridge URL + Secret (sama dengan .env)

### 3. Tambah akun (semua platform di satu tempat!)
Social Recorder → Accounts → New → pilih **Platform**, isi credential sesuai:

| Platform | Yang diisi | Login |
|----------|-----------|-------|
| WhatsApp | (kosong, cukup label) | Scan QR |
| Telegram | Phone (+62…) | OTP → 2FA |
| Discord | Token | Langsung |
| Instagram | Username + Password | Challenge code |
| Threads | Token (mode api) / Username (mode scrape) + Extra=api/scrape | Langsung |

Klik **Connect**. Status & QR/OTP muncul di form. Semua pesan masuk ke **All Messages**, filter per platform.

## Catatan
- 1 Bridge Secret untuk semua → rapi, tapi kalau bocor semua kena. Trade-off yang kamu pilih.
- Discord & Instagram berisiko ban — pakai akun alternatif.
- Telegram: set TG_FETCH_HISTORY=true untuk ambil history lama (mulai limit 200).

## Migrasi dari module terpisah
Module lama (wa_group_recorder dll) tetap bisa dipakai — yang ini menggantikan semuanya
dengan satu backend. Tidak perlu uninstall yang lama, tapi disarankan pilih salah satu
biar tidak dobel-record (port beda: lama 3000-3500, ini 3000 saja).
