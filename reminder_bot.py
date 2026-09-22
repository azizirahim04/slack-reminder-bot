"""
Slack Reminder Bot
-------------------
Mengecek pesan (top-level dan reply di dalam thread) di channel tertentu,
BERDASARKAN HASHTAG di teks pesannya, dengan 3 aturan:

1. Hashtag #new  + belum react 👀 (eyes)            -> reminder ke Wilma
   - trigger pertama: NEW_EYES_WINDOW_DAYS hari (default 1)
   - berulang tiap:   NEW_EYES_REPEAT_DAYS hari (default = window)

2. Hashtag #new  + belum react ✅ (white_check_mark) -> reminder ke Helmi
   - trigger pertama: NEW_CHECK_WINDOW_DAYS hari (default 7)
   - berulang tiap:   NEW_CHECK_REPEAT_DAYS hari (default = window)

3. Hashtag #urgent + belum react 👍 (+1/thumbsup)    -> reminder ke tim (user group)
   - trigger pertama: URGENT_WINDOW_DAYS hari (default 1)
   - berulang tiap:   URGENT_REPEAT_DAYS hari (default = window)

Pesan yang TIDAK mengandung #new atau #urgent tidak pernah kena reminder
sama sekali (bukan lagi memantau seluruh isi channel).

ANTI-DUPLIKAT & PENGULANGAN: setiap reminder disisipi marker tersembunyi
di akhir teks. Sebelum kirim, bot cek kapan reminder dengan marker itu
TERAKHIR kali dikirim -- kalau belum pernah, kirim sekarang; kalau sudah
pernah, kirim lagi HANYA setelah lewat *_REPEAT_DAYS sejak reminder
terakhir. Jadi run manual berkali-kali tidak spam, tapi tetap reminder
berulang berkala selama belum di-react.

CATATAN PENTING soal hashtag: kalau di workspace kamu ada channel Slack
yang namanya benar-benar "new" atau "urgent", Slack bisa otomatis
mengubah teks "#new"/"#urgent" jadi link channel (bukan teks hashtag
biasa) saat pesan dikirim. Kalau reminder tidak terpicu padahal sudah
ada hashtag di pesan, ini kemungkinan penyebabnya -- kabari untuk
disesuaikan filternya.

Environment variables:
- SLACK_BOT_TOKEN     : Bot token Slack (xoxb-...)
- SLACK_CHANNEL_IDS   : ID channel yang mau dipantau, pisahkan dengan koma
- MAX_LOOKBACK_DAYS   : (opsional) batas maksimum penelusuran pesan/thread lama, default 30

- NEW_TAG             : (opsional) teks hashtag aturan #new, default "#new"
- NEW_EYES_WINDOW_DAYS, NEW_EYES_REPEAT_DAYS : lihat di atas
- NEW_EYES_MENTION_USER_ID : User ID Wilma (WAJIB diisi untuk aturan ini)
- NEW_EYES_TEXT       : (opsional) template teks. {mention} & {days} tersedia.

- NEW_CHECK_WINDOW_DAYS, NEW_CHECK_REPEAT_DAYS : lihat di atas
- NEW_CHECK_MENTION_USER_ID : User ID Helmi (WAJIB diisi untuk aturan ini)
- NEW_CHECK_TEXT      : (opsional) template teks.

- URGENT_TAG          : (opsional) teks hashtag aturan #urgent, default "#urgent"
- URGENT_WINDOW_DAYS, URGENT_REPEAT_DAYS : lihat di atas
- URGENT_MENTION_GROUP_ID : ID user group tim (misal @team-klaimsj)
- URGENT_TEXT         : (opsional) template teks.
"""

import os
import time
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_IDS = os.environ.get("SLACK_CHANNEL_IDS", "")
MAX_LOOKBACK_DAYS = int(os.environ.get("MAX_LOOKBACK_DAYS", "30"))

SECONDS_PER_DAY = 86400

# --- Aturan 1: #new + 👀 -> Wilma ---
NEW_TAG = os.environ.get("NEW_TAG", "#new")
NEW_EYES_WINDOW_DAYS = int(os.environ.get("NEW_EYES_WINDOW_DAYS", "1"))
NEW_EYES_REPEAT_DAYS = int(os.environ.get("NEW_EYES_REPEAT_DAYS", str(NEW_EYES_WINDOW_DAYS)))
NEW_EYES_MENTION_USER_ID = os.environ.get("NEW_EYES_MENTION_USER_ID", "")
NEW_EYES_TEXT = os.environ.get(
    "NEW_EYES_TEXT",
    "👀 {mention} pesan #new ini sudah {days} hari belum di-react 👀 (belum dilihat). Mohon dicek ya!",
)

# --- Aturan 2: #new + ✅ -> Helmi ---
NEW_CHECK_WINDOW_DAYS = int(os.environ.get("NEW_CHECK_WINDOW_DAYS", "7"))
NEW_CHECK_REPEAT_DAYS = int(os.environ.get("NEW_CHECK_REPEAT_DAYS", str(NEW_CHECK_WINDOW_DAYS)))
NEW_CHECK_MENTION_USER_ID = os.environ.get("NEW_CHECK_MENTION_USER_ID", "")
NEW_CHECK_TEXT = os.environ.get(
    "NEW_CHECK_TEXT",
    "✅ {mention} pesan #new ini sudah {days} hari belum di-react ✅ (belum di-checklist/selesai). Mohon ditindaklanjuti ya!",
)

# --- Aturan 3: #urgent + 👍 -> tim (user group) ---
URGENT_TAG = os.environ.get("URGENT_TAG", "#urgent")
URGENT_WINDOW_DAYS = int(os.environ.get("URGENT_WINDOW_DAYS", "1"))
URGENT_REPEAT_DAYS = int(os.environ.get("URGENT_REPEAT_DAYS", str(URGENT_WINDOW_DAYS)))
URGENT_MENTION_GROUP_ID = os.environ.get("REMINDER_MENTION_GROUP_ID", "")
URGENT_TEXT = os.environ.get(
    "URGENT_TEXT",
    "👍 {mention} pesan #urgent ini sudah {days} hari belum di-react 👍. Mohon segera direspon!",
)

THUMBSUP_NAMES = {"+1", "thumbsup"}


def fixed_mention(user_id="", group_id=""):
    if group_id:
        return f"<!subteam^{group_id}>"
    if user_id:
        return f"<@{user_id}>"
    return ""


RULES = [
    {
        "code": "NEWEYES",
        "label": "#new belum dilihat (👀 -> Wilma)",
        "tag": NEW_TAG,
        "emoji_names": {"eyes"},
        "window_days": NEW_EYES_WINDOW_DAYS,
        "repeat_days": NEW_EYES_REPEAT_DAYS,
        "mention": fixed_mention(user_id=NEW_EYES_MENTION_USER_ID),
        "text_template": NEW_EYES_TEXT,
    },
    {
        "code": "NEWCHECK",
        "label": "#new belum checklist (✅ -> Helmi)",
        "tag": NEW_TAG,
        "emoji_names": {"white_check_mark"},
        "window_days": NEW_CHECK_WINDOW_DAYS,
        "repeat_days": NEW_CHECK_REPEAT_DAYS,
        "mention": fixed_mention(user_id=NEW_CHECK_MENTION_USER_ID),
        "text_template": NEW_CHECK_TEXT,
    },
    {
        "code": "URGENT",
        "label": "#urgent belum di-ack (👍 -> tim)",
        "tag": URGENT_TAG,
        "emoji_names": THUMBSUP_NAMES,
        "window_days": URGENT_WINDOW_DAYS,
        "repeat_days": URGENT_REPEAT_DAYS,
        "mention": fixed_mention(group_id=URGENT_MENTION_GROUP_ID),
        "text_template": URGENT_TEXT,
    },
]


def get_channel_ids():
    ids = [c.strip() for c in SLACK_CHANNEL_IDS.split(",") if c.strip()]
    if not ids:
        print("ERROR: SLACK_CHANNEL_IDS kosong. Set minimal satu channel ID.")
        sys.exit(1)
    return ids


def has_reaction(msg, emoji_names):
    reactions = msg.get("reactions", [])
    names_present = {r["name"] for r in reactions}
    return bool(names_present & emoji_names)


def message_has_tag(msg, tag):
    if not tag:
        return True
    return tag.lower() in msg.get("text", "").lower()


def build_marker(rule_code, target_ts):
    return f"ref:{rule_code}-{target_ts}"


def fetch_all_thread_messages(client, channel_id, thread_ts):
    all_messages = []
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel_id, ts=thread_ts, cursor=cursor, limit=200
        )
        all_messages.extend(resp.get("messages", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return all_messages


def get_last_reminder_ts(client, channel_id, thread_ts, marker):
    try:
        messages = fetch_all_thread_messages(client, channel_id, thread_ts)
    except SlackApiError as e:
        print(f"  !! Gagal cek riwayat reminder (anggap belum pernah): {e.response['error']}")
        return None
    times = [float(m["ts"]) for m in messages if marker in m.get("text", "")]
    return max(times) if times else None


def send_reminder(client, channel_id, thread_ts, target_msg, rule):
    target_ts = target_msg["ts"]
    marker = build_marker(rule["code"], target_ts)

    last_reminder_ts = get_last_reminder_ts(client, channel_id, thread_ts, marker)
    if last_reminder_ts is not None:
        elapsed_days = (time.time() - last_reminder_ts) / SECONDS_PER_DAY
        if elapsed_days < rule["repeat_days"]:
            print(f"  .. Lewati ts={target_ts}: [{rule['label']}] terakhir baru {elapsed_days:.1f} hari lalu (perlu >= {rule['repeat_days']} hari)")
            return False

    base_text = rule["text_template"].format(mention=rule["mention"], days=rule["window_days"]).strip()
    text = f"{base_text}\n_{marker}_"

    try:
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
        print(f"  -> Reminder [{rule['label']}] terkirim untuk pesan ts={target_ts}")
        return True
    except SlackApiError as e:
        print(f"  !! Gagal kirim reminder untuk ts={target_ts}: {e.response['error']}")
        return False


def fetch_top_level_messages_due(client, channel_id, window_days, max_lookback_days, tag):
    now = time.time()
    oldest = now - max_lookback_days * SECONDS_PER_DAY
    latest = now - window_days * SECONDS_PER_DAY

    all_messages = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id, oldest=str(oldest), latest=str(latest),
            inclusive=True, cursor=cursor, limit=200,
        )
        for msg in resp.get("messages", []):
            if msg.get("subtype") is not None:
                continue
            if not message_has_tag(msg, tag):
                continue
            all_messages.append(msg)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return all_messages


def fetch_threads_with_replies(client, channel_id, max_lookback_days):
    now = time.time()
    oldest = now - max_lookback_days * SECONDS_PER_DAY

    thread_ts_list = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id, oldest=str(oldest), cursor=cursor, limit=200
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


def fetch_replies_due(client, channel_id, thread_ts, window_days, max_lookback_days, tag):
    now = time.time()
    oldest = now - max_lookback_days * SECONDS_PER_DAY
    latest = now - window_days * SECONDS_PER_DAY

    replies = []
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel_id, ts=thread_ts, cursor=cursor, limit=200
        )
        for msg in resp.get("messages", []):
            if msg["ts"] == thread_ts:
                continue
            if msg.get("subtype") is not None:
                continue
            if not message_has_tag(msg, tag):
                continue
            msg_ts_f = float(msg["ts"])
            if oldest <= msg_ts_f <= latest:
                replies.append(msg)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return replies


def run_rule(client, channel_id, rule):
    print(f"\n--- [{rule['label']}] channel {channel_id}, tag={rule['tag']}, window>={rule['window_days']}hr, ulang tiap {rule['repeat_days']}hr ---")

    if not rule["mention"]:
        print(f"  !! PERINGATAN: mention untuk aturan ini kosong (secret belum diisi). Reminder tetap dikirim tanpa mention.")

    checked = 0
    reminded = 0
    skipped = 0

    # --- 1. Pesan top-level ---
    try:
        top_messages = fetch_top_level_messages_due(client, channel_id, rule["window_days"], MAX_LOOKBACK_DAYS, rule["tag"])
    except SlackApiError as e:
        print(f"  ERROR mengambil history: {e.response['error']}")
        top_messages = []

    for msg in top_messages:
        checked += 1
        if has_reaction(msg, rule["emoji_names"]):
            continue
        sent = send_reminder(client, channel_id, msg["ts"], msg, rule)
        reminded += 1 if sent else 0
        skipped += 0 if sent else 1
        time.sleep(1.2)

    # --- 2. Reply di dalam thread ---
    try:
        thread_ts_list = fetch_threads_with_replies(client, channel_id, MAX_LOOKBACK_DAYS)
    except SlackApiError as e:
        print(f"  ERROR mencari thread: {e.response['error']}")
        thread_ts_list = []

    for thread_ts in thread_ts_list:
        try:
            replies = fetch_replies_due(client, channel_id, thread_ts, rule["window_days"], MAX_LOOKBACK_DAYS, rule["tag"])
        except SlackApiError as e:
            print(f"  ERROR mengambil replies thread {thread_ts}: {e.response['error']}")
            continue

        for reply in replies:
            checked += 1
            if has_reaction(reply, rule["emoji_names"]):
                continue
            sent = send_reminder(client, channel_id, thread_ts, reply, rule)
            reminded += 1 if sent else 0
            skipped += 0 if sent else 1
            time.sleep(1.2)

        time.sleep(0.3)

    print(f"[{rule['label']}] Selesai. Dicek: {checked}, reminder terkirim: {reminded}, belum waktunya/dilewati: {skipped}")


def check_channel(client: WebClient, channel_id: str):
    for rule in RULES:
        run_rule(client, channel_id, rule)


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
