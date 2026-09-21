"""
Slack Reminder Bot
-------------------
Mengecek pesan-pesan di channel tertentu dengan DUA aturan terpisah:

1. React 👀 (eyes)         : kalau dalam EYES_WINDOW_DAYS (default 1 hari)
                              belum ada react mata, bot reply reminder
                              "belum dilihat" di thread, sambil mention
                              orang/tim yang dikonfigurasi.
2. React ✅ (white_check_mark): kalau dalam CHECK_WINDOW_DAYS (default 7 hari)
                              belum ada react ceklis, bot reply reminder
                              "belum di-checklist" di thread, sambil mention
                              orang/tim yang dikonfigurasi.

Kedua aturan ini independen -- satu pesan/reply bisa kena reminder mata
di hari 1, lalu kena reminder ceklis lagi di hari 7 kalau masih belum
di-checklist.

CAKUPAN: script ini mengecek SEMUA pesan reguler di channel yang
didaftarkan -- baik pesan utama (top-level) MAUPUN reply di dalam thread.
Untuk reply di dalam thread, script menelusuri thread-thread yang punya
balasan dalam THREAD_LOOKBACK_DAYS hari terakhir (default 30 hari),
supaya reply baru di thread lama tetap kedeteksi.

Script ini didesain untuk dijalankan sekali sehari (lewat cron job atau
GitHub Actions terjadwal). Untuk tiap aturan, ia mengecek pesan dalam
jendela waktu [N hari lalu, N+1 hari lalu], jadi tiap pesan/reply hanya
"dilewati" sekali per aturan -- tidak perlu database/status sendiri.

Environment variables yang dibutuhkan:
- SLACK_BOT_TOKEN     : Bot token Slack (xoxb-...)
- SLACK_CHANNEL_IDS   : ID channel yang mau dipantau, pisahkan dengan koma
                         Contoh: "C0123456789,C0987654321"
- EYES_WINDOW_DAYS    : (opsional) jumlah hari sebelum reminder "belum dilihat", default 1
- CHECK_WINDOW_DAYS   : (opsional) jumlah hari sebelum reminder "belum di-checklist", default 7
- THREAD_LOOKBACK_DAYS: (opsional) seberapa jauh ke belakang menelusuri thread lama
                         untuk mencari reply baru, default 30 hari
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
THREAD_LOOKBACK_DAYS = int(os.environ.get("THREAD_LOOKBACK_DAYS", "30"))

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
    3. fallback -> mention pengirim pesan/reply asli
    """
    if REMINDER_MENTION_GROUP_ID:
        return f"<!subteam^{REMINDER_MENTION_GROUP_ID}>"

    if REMINDER_MENTION_USER_ID:
        return f"<@{REMINDER_MENTION_USER_ID}>"

    user_id = msg.get("user")
    return f"<@{user_id}>" if user_id else ""


def send_reminder(client, channel_id, thread_ts, msg, text_template, window_days, rule_label):
    """Kirim satu reminder sebagai reply di thread_ts. Dipakai baik untuk
    pesan top-level maupun reply di dalam thread."""
    mention = build_mention(msg)
    text = text_template.format(mention=mention, days=window_days).strip()

    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
        )
        print(f"  -> Reminder [{rule_label}] terkirim untuk pesan ts={msg['ts']}")
        return True
    except SlackApiError as e:
        print(f"  !! Gagal kirim reminder untuk ts={msg['ts']}: {e.response['error']}")
        return False


# ---------------------------------------------------------------------
# Bagian 1: pesan top-level (langsung di channel, bukan reply thread)
# ---------------------------------------------------------------------

def fetch_top_level_messages_in_window(client, channel_id, window_days):
    """Ambil semua pesan top-level (non-subtype) yang umurnya antara
    window_days dan (window_days + 1) hari yang lalu."""
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


# ---------------------------------------------------------------------
# Bagian 2: reply di dalam thread
# ---------------------------------------------------------------------

def fetch_threads_with_replies(client, channel_id, lookback_days):
    """Cari semua pesan top-level yang punya reply (thread aktif), dalam
    lookback_days terakhir. Mengembalikan list thread_ts."""
    now = time.time()
    oldest = now - lookback_days * SECONDS_PER_DAY

    thread_ts_list = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest),
            cursor=cursor,
            limit=200,
        )
        for msg in resp.get("messages", []):
            if msg.get("subtype") is not None:
                continue
            if msg.get("reply_count", 0) > 0:
                thread_ts_list.append(msg["ts"])

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    return thread_ts_list


def fetch_replies_in_window(client, channel_id, thread_ts, window_days):
    """Ambil reply-reply (bukan pesan induk) di satu thread yang umurnya
    antara window_days dan (window_days + 1) hari yang lalu."""
    now = time.time()
    oldest = now - (window_days + 1) * SECONDS_PER_DAY
    latest = now - window_days * SECONDS_PER_DAY

    replies = []
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            cursor=cursor,
            limit=200,
        )
        for msg in resp.get("messages", []):
            if msg["ts"] == thread_ts:
                continue  # lewati pesan induk, sudah dicek di bagian top-level
            if msg.get("subtype") is not None:
                continue
            msg_ts_f = float(msg["ts"])
            if oldest <= msg_ts_f <= latest:
                replies.append(msg)

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    return replies


# ---------------------------------------------------------------------
# Runner utama per aturan
# ---------------------------------------------------------------------

def run_rule(client, channel_id, window_days, emoji_name, text_template, rule_label):
    """Jalankan satu aturan reminder untuk SEMUA pesan (top-level + reply
    thread) di channel_id: cek pesan/reply di window_days hari lalu, kalau
    belum ada emoji_name, kirim reminder pakai text_template."""
    print(f"\n--- [{rule_label}] channel {channel_id}, window {window_days}-{window_days + 1} hari ---")

    checked = 0
    reminded = 0

    # --- 1. Pesan top-level ---
    try:
        top_messages = fetch_top_level_messages_in_window(client, channel_id, window_days)
    except SlackApiError as e:
        print(f"  ERROR mengambil history: {e.response['error']}")
        top_messages = []

    for msg in top_messages:
        checked += 1
        if has_reaction(msg, emoji_name):
            continue
        if send_reminder(client, channel_id, msg["ts"], msg, text_template, window_days, rule_label):
            reminded += 1
        time.sleep(1.2)  # hindari rate limit

    # --- 2. Reply di dalam thread ---
    try:
        thread_ts_list = fetch_threads_with_replies(client, channel_id, THREAD_LOOKBACK_DAYS)
    except SlackApiError as e:
        print(f"  ERROR mencari thread: {e.response['error']}")
        thread_ts_list = []

    for thread_ts in thread_ts_list:
        try:
            replies = fetch_replies_in_window(client, channel_id, thread_ts, window_days)
        except SlackApiError as e:
            print(f"  ERROR mengambil replies thread {thread_ts}: {e.response['error']}")
            continue

        for reply in replies:
            checked += 1
            if has_reaction(reply, emoji_name):
                continue
            # thread_ts (pesan induk) dipakai supaya reminder masuk ke thread yang sama
            if send_reminder(client, channel_id, thread_ts, reply, text_template, window_days, rule_label):
                reminded += 1
            time.sleep(1.2)

        time.sleep(0.3)  # jeda kecil antar thread

    print(f"[{rule_label}] Selesai. Pesan+reply dicek: {checked}, reminder dikirim: {reminded}")


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
