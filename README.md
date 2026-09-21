# Slack Reminder Bot — react ✅ / 👀 dalam 3 hari

Bot ini mengecek pesan Slack yang sudah berumur 3 hari. Kalau pesan itu
belum di-react dengan ✅ (white_check_mark) atau 👀 (eyes), bot akan
membalas reminder di **thread** pesan tersebut. Dijalankan otomatis
setiap hari lewat GitHub Actions — gratis, tanpa server sendiri.

## 1. Buat Slack App & Bot Token

1. Buka https://api.slack.com/apps → **Create New App** → **From scratch**.
2. Kasih nama app (bebas) dan pilih workspace kamu.
   *(Catatan: kalau workspace kamu Free/Pro/Business+, siapa pun member
   dengan izin instal app bisa lakukan ini sendiri tanpa approval admin.
   Kalau Enterprise Grid, kemungkinan tetap perlu persetujuan admin.)*
3. Di menu kiri, klik **OAuth & Permissions**.
4. Scroll ke **Scopes → Bot Token Scopes**, tambahkan scope berikut:
   - `channels:history` (baca pesan channel publik)
   - `groups:history` (baca pesan channel privat, kalau perlu)
   - `reactions:read` (baca reaction pada pesan)
   - `chat:write` (kirim pesan/reply)
5. Scroll ke atas, klik **Install to Workspace** → **Allow**.
6. Copy **Bot User OAuth Token** (diawali `xoxb-...`) — ini akan jadi
   `SLACK_BOT_TOKEN`.
7. Undang bot ke channel yang mau dipantau: di channel Slack, ketik
   `/invite @NamaBotKamu`.

## 2. Cari Channel ID

- Buka channel di Slack (versi desktop/web) → klik nama channel di
  bagian atas → scroll ke bawah, Channel ID ada di situ (format `C0123456789`).
- Bisa pantau lebih dari satu channel, pisahkan dengan koma.

## 3. Push project ini ke repo GitHub kamu

```bash
cd slack-reminder-bot
git init
git add .
git commit -m "Setup Slack reminder bot"
git remote add origin <URL_REPO_GITHUB_KAMU>
git push -u origin main
```

Repo boleh **private** — GitHub Actions tetap jalan gratis untuk repo
private dengan kuota menit tertentu per bulan (lebih dari cukup untuk
script sesingkat ini, karena hanya jalan sebentar sekali sehari).

## 4. Set Secrets di GitHub

Di repo GitHub kamu: **Settings → Secrets and variables → Actions → New repository secret**

Tambahkan 2 secret:
- `SLACK_BOT_TOKEN` → token `xoxb-...` dari langkah 1
- `SLACK_CHANNEL_IDS` → channel ID, pisahkan koma kalau lebih dari satu
  (contoh: `C0123456789,C0987654321`)

## 5. Selesai — testing

- Workflow otomatis jalan tiap hari jam 09:00 WIB (bisa diubah di
  `.github/workflows/remind.yml`, baris cron).
- Untuk testing tanpa nunggu jadwal: buka tab **Actions** di repo GitHub
  kamu → pilih workflow **Slack Reminder Bot** → klik **Run workflow**
  (tombol ini muncul karena ada `workflow_dispatch` di file yml).

## Cara kerja & batasan

- Script cek pesan yang umurnya **3–4 hari** (window 1 hari) setiap
  kali jalan, supaya tiap pesan cuma "dilewati" sekali di sekitar hari
  ke-3 — tanpa perlu database/status tersendiri.
- Kalau workflow gagal jalan tepat sehari (misal GitHub Actions delay),
  pesan yang "kelewat" tidak akan dicek ulang di hari berikutnya karena
  sudah lewat window. Kalau kamu mau lebih toleran, ubah
  `CHECK_WINDOW_DAYS` atau perbesar window di `reminder_bot.py`.
- Reminder dikirim sebagai **reply di thread** pesan asli (bukan DM
  atau pesan channel terpisah).
- Kalau mau ubah teks reminder, set secret/env `REMINDER_TEXT`.

## Menjalankan lokal (opsional, untuk testing)

```bash
pip install -r requirements.txt
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_CHANNEL_IDS="C0123456789"
python reminder_bot.py
```
