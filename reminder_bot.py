"""
Slack Reminder Bot
-------------------
Mengecek pesan-pesan di channel tertentu dengan DUA aturan terpisah:

1. React 👀 (eyes)         : kalau dalam EYES_WINDOW_DAYS (default 1 hari)
                              belum ada react mata, bot reply reminder
                              "belum dilihat" di thread, sambil mention
                              pengirim pesan asli.
2. React ✅ (white_check_mark): kalau dalam CHECK_WINDOW_DAYS (default 7 hari)
                              belum ada react ceklis, bot reply reminder
                              "belum di-checklist" di thread, sambil mention
                              pengirim pesan asli.

Kedua aturan ini independen -- satu pesan bisa kena reminder mata di hari 1,
lalu kena reminder ceklis lagi di hari 7 kalau masih belum di-checklist.

Script ini didesain untuk dijalankan sekali sehari (lewat cron job atau
GitHub Actions terjadwal). Untuk tiap aturan, ia mengecek pesan dalam
jendela waktu [N hari lalu, N+1 hari lalu], jadi tiap pesan hanya
"dilewati" sekali per aturan -- tidak perlu database/status sendiri.

Environment variables yang dibutuhkan:
- SLACK_BOT_TOKEN     : Bot token Slack (xoxb-...)
- SLACK_CHANNEL_IDS   : ID channel yang mau dipantau, pisahkan dengan koma
                         Contoh: "C0123456789,C0987654321"
- EYES_WINDOW_DAYS    : (opsional) jumlah hari sebelum reminder "belum dilihat", default 1
- CHECK_WINDOW_DAYS   : (opsional) jumlah hari sebelum reminder "belum di-checklist", default 7
- EYES_REMINDER_TEXT  : (opsional) template teks reminder mata. {mention} akan diganti mention user.
- CHECK_REMINDER_TEXT : (opsional) template teks reminder ceklis. {mention} akan diganti mention user.
- REMINDER_MENTION_USER_ID : ID Slack orang (PIC/manager) yang di-mention di SEMUA
                              reminder, menggantikan pengirim pesan asli.
                              Contoh: "U0123456789". Cara cari ID ini: buka profil
                              orang tsb di Slack -> klik titik tiga (...) -> "Copy member ID".
- REMINDER_MENTION_GROUP_ID : ID user group / tim (misal @team-klaimsj) yang di-mention
                              di SEMUA reminder. Kalau diisi, ini PRIORITAS PALING TINGGI
                              (mengalahkan REMINDER_MENTION_USER_ID). Contoh: "S0123ABCDE".
                              Cara cari: People -> User groups -> klik grupnya -> lihat ID
                              di URL (diawali huruf S).
"""

import os
import time
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- Konfigurasi dari environment variables ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_IDS = os.environ.get("SLACK_CHANNEL_IDS", "")

EYES_WINDOW_DAYS = int(os.environ.get("EYES_WINDOW_DAYS", "1"))
CHECK_WINDOW_DAYS = int(os.environ.get("CHECK_WINDOW_DAYS", "7"))

EYES_REMINDER_TEXT = os.environ.get(
    "EYES_REMINDER_TEXT",
    "👀 {mention} pesan ini sudah {days} hari belum di-react 👀 (belum dilihat). Mohon dicek ya!",
)
CHECK_REMINDER_TEXT = os.environ.get(
    "CHECK_REMINDER_TEXT",
    "✅ {mention} pesan ini sudah {days} hari belum di-react ✅ (belum di-checklist/selesai). Mohon ditindaklanjuti ya!",
)

# Kalau diisi, SEMUA reminder akan mention user ID ini (PIC/manager tetap),
# menggantikan mention ke pengirim pesan asli.
REMINDER_MENTION_USER_ID = os.environ.get("REMINDER_MENTION_USER_ID", "")

# Kalau diisi, SEMUA reminder akan mention user GROUP ini (misal @team-klaimsj).
# Prioritas lebih tinggi dari REMINDER_MENTION_USER_ID.
REMINDER_MENTION_GROUP_ID = os.environ.get("REMINDER_MENTION_GROUP_ID", "")

SECONDS_PER_DAY = 86400


def get_channel_ids():
    ids = [c.strip() for c in SLACK_CHANNEL_IDS.split(",") if c.strip()]
    if not ids:
        print("ERROR: SLACK_CHANNEL_IDS kosong. Set minimal satu channel ID.")
        sys.exit(1)
    return ids


def has_reaction(msg, emoji_name):
    reactions = msg.get("reactions", [])
    return any(r["name"] == emoji_name for r in reactions)


def build_mention(msg):
    """Mention orang/group di reminder. Prioritas:
    1. REMINDER_MENTION_GROUP_ID  -> mention user group (misal @team-klaimsj)
    2. REMINDER_MENTION_USER_ID   -> mention satu orang tetap (PIC/manager)
    3. fallback -> mention pengirim pesan asli
    """
    if REMINDER_MENTION_GROUP_ID:
        return f"<!subteam^{REMINDER_MENTION_GROUP_ID}>"

    if REMINDER_MENTION_USER_ID:
        return f"<@{REMINDER_MENTION_USER_ID}>"

    user_id = msg.get("user")
    return f"<@{user_id}>" if user_id else ""


def fetch_messages_in_window(client, channel_id, window_days):
    """Ambil semua pesan (non-subtype) yang umurnya antara window_days
    dan (window_days + 1) hari yang lalu."""
    now = time.time()
    oldest = now - (window_days + 1) * SECONDS_PER_DAY
    latest = now - window_days * SECONDS_PER_DAY

    all_messages = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest),
            latest=str(latest),
            inclusive=True,
            cursor=cursor,
            limit=200,
        )
        for msg in resp.get("messages", []):
            if msg.get("subtype") is not None:
                continue
            all_messages.append(msg)

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    return all_messages


def run_rule(client, channel_id, window_days, emoji_name, text_template, rule_label):
    """Jalankan satu aturan reminder: cek pesan di window_days hari lalu,
    kalau belum ada emoji_name, kirim reminder pakai text_template."""
    print(f"\n--- [{rule_label}] channel {channel_id}, window {window_days}-{window_days + 1} hari ---")

    try:
        messages = fetch_messages_in_window(client, channel_id, window_days)
    except SlackApiError as e:
        print(f"  ERROR mengambil history: {e.response['error']}")
        return

    checked = 0
    reminded = 0

    for msg in messages:
        checked += 1

        if has_reaction(msg, emoji_name):
            continue  # sudah di-react, tidak perlu reminder

        ts = msg["ts"]
        mention = build_mention(msg)
        text = text_template.format(mention=mention, days=window_days).strip()

        try:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=ts,
                text=text,
            )
            reminded += 1
            print(f"  -> Reminder [{rule_label}] terkirim untuk pesan ts={ts}")
        except SlackApiError as e:
            print(f"  !! Gagal kirim reminder untuk ts={ts}: {e.response['error']}")

        # Hindari rate limit Slack (Tier 3: ~1 request/detik aman)
        time.sleep(1.2)

    print(f"[{rule_label}] Selesai. Pesan dicek: {checked}, reminder dikirim: {reminded}")


def check_channel(client: WebClient, channel_id: str):
    # Aturan 1: react mata dalam EYES_WINDOW_DAYS hari
    run_rule(
        client,
        channel_id,
        EYES_WINDOW_DAYS,
        "eyes",
        EYES_REMINDER_TEXT,
        "belum dilihat (👀)",
    )

    # Aturan 2: react ceklis dalam CHECK_WINDOW_DAYS hari
    run_rule(
        client,
        channel_id,
        CHECK_WINDOW_DAYS,
        "white_check_mark",
        CHECK_REMINDER_TEXT,
        "belum checklist (✅)",
    )


def main():
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN belum diset.")
        sys.exit(1)

    client = WebClient(token=SLACK_BOT_TOKEN)
    channel_ids = get_channel_ids()

    for channel_id in channel_ids:
        print(f"\n=== Channel {channel_id} ===")
        check_channel(client, channel_id)


if __name__ == "__main__":
    main()
