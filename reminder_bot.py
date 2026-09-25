"""
Slack Reminder Bot
-------------------
Mengecek pesan (top-level dan reply di dalam thread) di channel tertentu,
BERDASARKAN HASHTAG di teks pesannya, dengan 3 aturan:

1. Hashtag #new  + belum react 👀 (eyes)            -> reminder ke Wilma
2. Hashtag #new  + belum react ✅ (white_check_mark) -> reminder ke Helmi
3. Hashtag #urgent + belum react 👍 (+1/thumbsup)    -> reminder ke tim (user group)

Tiap aturan trigger pertama setelah *_WINDOW_DAYS, lalu berulang tiap
*_REPEAT_DAYS selama masih belum di-react.

OPTIMASI: channel history dan isi tiap thread hanya di-fetch SATU KALI
per run (di-cache), lalu dipakai bersama oleh ketiga aturan.

DIAGNOSTIC: di awal tiap channel, script mencoba conversations_info
untuk menampilkan NAMA channel yang benar-benar dibaca (butuh scope
channels:read / groups:read). Ini membantu memastikan SLACK_CHANNEL_IDS
menunjuk ke channel yang benar.

Environment variables:
- SLACK_BOT_TOKEN, SLACK_CHANNEL_IDS, MAX_LOOKBACK_DAYS (default 30)
- NEW_TAG (default "#new"), NEW_EYES_WINDOW_DAYS (1), NEW_EYES_REPEAT_DAYS (=window),
  NEW_EYES_MENTION_USER_ID, NEW_EYES_TEXT
- NEW_CHECK_WINDOW_DAYS (7), NEW_CHECK_REPEAT_DAYS (=window),
  NEW_CHECK_MENTION_USER_ID, NEW_CHECK_TEXT
- URGENT_TAG (default "#urgent"), URGENT_WINDOW_DAYS (1), URGENT_REPEAT_DAYS (=window),
  REMINDER_MENTION_GROUP_ID, URGENT_TEXT
"""

import os
import time
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_IDS = os.environ.get("SLACK_CHANNEL_IDS", "")
SECONDS_PER_DAY = 86400


def get_int_env(name, default):
    val = os.environ.get(name, "").strip()
    if not val:
        return int(default)
    return int(val)


MAX_LOOKBACK_DAYS = get_int_env("MAX_LOOKBACK_DAYS", 30)

# --- Aturan 1: #new + 👀 -> Wilma ---
NEW_TAG = os.environ.get("NEW_TAG", "#new")
NEW_EYES_WINDOW_DAYS = get_int_env("NEW_EYES_WINDOW_DAYS", 1)
NEW_EYES_REPEAT_DAYS = get_int_env("NEW_EYES_REPEAT_DAYS", NEW_EYES_WINDOW_DAYS)
NEW_EYES_MENTION_USER_ID = os.environ.get("NEW_EYES_MENTION_USER_ID", "")
NEW_EYES_TEXT = os.environ.get(
    "NEW_EYES_TEXT",
    "👀 {mention} pesan #new ini sudah {days} hari belum di-react 👀 (belum dilihat). Mohon dicek ya!",
)

# --- Aturan 2: #new + ✅ -> Helmi ---
NEW_CHECK_WINDOW_DAYS = get_int_env("NEW_CHECK_WINDOW_DAYS", 7)
NEW_CHECK_REPEAT_DAYS = get_int_env("NEW_CHECK_REPEAT_DAYS", NEW_CHECK_WINDOW_DAYS)
NEW_CHECK_MENTION_USER_ID = os.environ.get("NEW_CHECK_MENTION_USER_ID", "")
NEW_CHECK_TEXT = os.environ.get(
    "NEW_CHECK_TEXT",
    "✅ {mention} pesan #new ini sudah {days} hari belum di-react ✅ (belum di-checklist/selesai). Mohon ditindaklanjuti ya!",
)

# --- Aturan 3: #urgent + 👍 -> tim (user group) ---
URGENT_TAG = os.environ.get("URGENT_TAG", "#urgent")
URGENT_WINDOW_DAYS = get_int_env("URGENT_WINDOW_DAYS", 1)
URGENT_REPEAT_DAYS = get_int_env("URGENT_REPEAT_DAYS", URGENT_WINDOW_DAYS)
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


def fetch_all_top_level_messages(client, channel_id, max_lookback_days):
    now = time.time()
    oldest = now - max_lookback_days * SECONDS_PER_DAY

    all_messages = []
    cursor = None
    page_num = 0
    while True:
        resp = client.conversations_history(
            channel=channel_id, oldest=str(oldest), cursor=cursor, limit=200
        )
        page_num += 1
        print(f"  DEBUG conversations_history page {page_num}: ok={resp.get('ok')}, warning={resp.get('warning')}, jumlah_pesan_di_page_ini={len(resp.get('messages', []))}, has_more={resp.get('has_more')}")
        for msg in resp.get("messages", []):
            if msg.get("subtype") is not None:
                continue
            all_messages.append(msg)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return all_messages


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


class ThreadCache:
    def __init__(self, client, channel_id):
        self.client = client
        self.channel_id = channel_id
        self._cache = {}

    def get(self, thread_ts):
        if thread_ts not in self._cache:
            try:
                self._cache[thread_ts] = fetch_all_thread_messages(self.client, self.channel_id, thread_ts)
            except SlackApiError as e:
                print(f"  !! Gagal fetch thread {thread_ts}: {e.response['error']}")
                self._cache[thread_ts] = []
        return self._cache[thread_ts]


def get_last_reminder_ts(thread_messages, marker):
    times = [float(m["ts"]) for m in thread_messages if marker in m.get("text", "")]
    return max(times) if times else None


def send_reminder(client, channel_id, thread_ts, target_msg, rule, thread_messages):
    target_ts = target_msg["ts"]
    marker = build_marker(rule["code"], target_ts)

    last_reminder_ts = get_last_reminder_ts(thread_messages, marker)
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


def run_rule(client, channel_id, rule, top_messages, thread_ts_list, thread_cache):
    print(f"\n--- [{rule['label']}] channel {channel_id}, tag={rule['tag']}, window>={rule['window_days']}hr, ulang tiap {rule['repeat_days']}hr ---")

    if not rule["mention"]:
        print("  !! PERINGATAN: mention untuk aturan ini kosong (secret belum diisi). Reminder tetap dikirim tanpa mention.")

    checked = 0
    reminded = 0
    skipped = 0
    now = time.time()
    latest_allowed = now - rule["window_days"] * SECONDS_PER_DAY

    for msg in top_messages:
        ts_f = float(msg["ts"])
        if ts_f > latest_allowed:
            continue
        if not message_has_tag(msg, rule["tag"]):
            continue
        checked += 1
        if has_reaction(msg, rule["emoji_names"]):
            continue

        thread_messages = thread_cache.get(msg["ts"])
        sent = send_reminder(client, channel_id, msg["ts"], msg, rule, thread_messages)
        reminded += 1 if sent else 0
        skipped += 0 if sent else 1
        if sent:
            time.sleep(1.2)

    for thread_ts in thread_ts_list:
        thread_messages = thread_cache.get(thread_ts)

        for reply in thread_messages:
            if reply["ts"] == thread_ts:
                continue
            if reply.get("subtype") is not None:
                continue
            ts_f = float(reply["ts"])
            if ts_f > latest_allowed:
                continue
            if not message_has_tag(reply, rule["tag"]):
                continue

            checked += 1
            if has_reaction(reply, rule["emoji_names"]):
                continue

            sent = send_reminder(client, channel_id, thread_ts, reply, rule, thread_messages)
            reminded += 1 if sent else 0
            skipped += 0 if sent else 1
            if sent:
                time.sleep(1.2)

    print(f"[{rule['label']}] Selesai. Dicek: {checked}, reminder terkirim: {reminded}, belum waktunya/dilewati: {skipped}")


def print_channel_diagnostic(client, channel_id):
    """Tampilkan NAMA channel yang benar-benar dibaca bot, supaya kelihatan
    kalau ternyata SLACK_CHANNEL_IDS menunjuk ke channel yang salah."""
    try:
        resp = client.conversations_info(channel=channel_id)
        info = resp.get("channel", {})
        name = info.get("name", "(tidak diketahui)")
        is_member = info.get("is_member", "?")
        is_private = info.get("is_private", "?")
        num_members = info.get("num_members", "?")
        print(f"DIAGNOSTIC: channel ID ini bernama '#{name}' | bot jadi member: {is_member} | privat: {is_private} | jumlah member: {num_members}")
    except SlackApiError as e:
        print(f"DIAGNOSTIC: gagal ambil info channel ({e.response['error']}). Kemungkinan channel ID salah, atau butuh scope channels:read/groups:read.")


def check_channel(client: WebClient, channel_id: str):
    print_channel_diagnostic(client, channel_id)

    print(f"DEBUG: MAX_LOOKBACK_DAYS yang kepakai = {MAX_LOOKBACK_DAYS}")
    print(f"Mengambil daftar pesan channel (sekali saja, dipakai untuk semua aturan)...")
    top_messages = fetch_all_top_level_messages(client, channel_id, MAX_LOOKBACK_DAYS)
    thread_ts_list = [m["ts"] for m in top_messages if m.get("reply_count", 0) > 0]
    thread_cache = ThreadCache(client, channel_id)

    print(f"Ditemukan {len(top_messages)} pesan top-level, {len(thread_ts_list)} thread aktif dalam {MAX_LOOKBACK_DAYS} hari terakhir.")

    for rule in RULES:
        run_rule(client, channel_id, rule, top_messages, thread_ts_list, thread_cache)


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
