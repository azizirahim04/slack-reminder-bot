"""
Slack Reminder Bot
-------------------
Mengecek pesan-pesan di channel tertentu yang usianya 3 hari.
Kalau pesan itu BELUM punya react ✅ (white_check_mark) ATAU 👀 (eyes),
bot akan reply reminder di thread pesan tersebut.

Script ini didesain untuk dijalankan sekali sehari (lewat cron job atau
GitHub Actions terjadwal). Ia mengecek pesan dalam jendela waktu
3-4 hari yang lalu, jadi setiap pesan hanya "dilewati" sekali di sekitar
hari ke-3 -- tidak perlu menyimpan status/database sendiri.

Environment variables yang dibutuhkan:
- SLACK_BOT_TOKEN     : Bot token Slack (xoxb-...)
- SLACK_CHANNEL_IDS   : ID channel yang mau dipantau, pisahkan dengan koma
                         Contoh: "C0123456789,C0987654321"
- REMINDER_TEXT       : (opsional) teks reminder custom
- CHECK_WINDOW_DAYS   : (opsional) jumlah hari sebelum reminder dikirim, default 3
"""

import os
import time
import sys
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- Konfigurasi dari environment variables ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_IDS = os.environ.get("SLACK_CHANNEL_IDS", "")
REMINDER_TEXT = os.environ.get(
    "REMINDER_TEXT",
    "⏰ Reminder: pesan ini sudah 3 hari belum di-react ✅ atau 👀. Mohon dicek ya!",
)
CHECK_WINDOW_DAYS = int(os.environ.get("CHECK_WINDOW_DAYS", "3"))

# Emoji yang dianggap "sudah ditangani" (nama emoji Slack, tanpa titik dua)
DONE_REACTIONS = {"white_check_mark", "eyes"}

SECONDS_PER_DAY = 86400


def get_channel_ids():
    ids = [c.strip() for c in SLACK_CHANNEL_IDS.split(",") if c.strip()]
    if not ids:
        print("ERROR: SLACK_CHANNEL_IDS kosong. Set minimal satu channel ID.")
        sys.exit(1)
    return ids


def message_has_done_reaction(msg):
    """Cek apakah pesan sudah punya react checkmark atau eyes."""
    reactions = msg.get("reactions", [])
    reaction_names = {r["name"] for r in reactions}
    return bool(reaction_names & DONE_REACTIONS)


def check_channel(client: WebClient, channel_id: str):
    now = time.time()
    # Jendela waktu: pesan yang umurnya antara CHECK_WINDOW_DAYS dan
    # (CHECK_WINDOW_DAYS + 1) hari yang lalu.
    oldest = now - (CHECK_WINDOW_DAYS + 1) * SECONDS_PER_DAY
    latest = now - CHECK_WINDOW_DAYS * SECONDS_PER_DAY

    print(f"\n--- Mengecek channel {channel_id} ---")
    print(f"Window: pesan antara {CHECK_WINDOW_DAYS}-{CHECK_WINDOW_DAYS + 1} hari lalu")

    try:
        cursor = None
        checked = 0
        reminded = 0
        while True:
            resp = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                latest=str(latest),
                inclusive=True,
                cursor=cursor,
                limit=200,
            )
            messages = resp.get("messages", [])

            for msg in messages:
                # Lewati pesan sistem (join channel, dsb) dan pesan dari bot lain
                if msg.get("subtype") is not None:
                    continue

                checked += 1

                if message_has_done_reaction(msg):
                    continue  # sudah di-react, tidak perlu reminder

                ts = msg["ts"]
                # thread_ts = ts pesan asli supaya reply masuk ke thread pesan itu
                try:
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=ts,
                        text=REMINDER_TEXT,
                    )
                    reminded += 1
                    print(f"  -> Reminder terkirim untuk pesan ts={ts}")
                except SlackApiError as e:
                    print(f"  !! Gagal kirim reminder untuk ts={ts}: {e.response['error']}")

                # Hindari rate limit Slack (Tier 3: ~1 request/detik aman)
                time.sleep(1.2)

            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        print(f"Selesai. Pesan dicek: {checked}, reminder dikirim: {reminded}")

    except SlackApiError as e:
        print(f"ERROR saat mengambil history channel {channel_id}: {e.response['error']}")


def main():
    if not SLACK_BOT_TOKEN:
        print("ERROR: SLACK_BOT_TOKEN belum diset.")
        sys.exit(1)

    client = WebClient(token=SLACK_BOT_TOKEN)
    channel_ids = get_channel_ids()

    for channel_id in channel_ids:
        check_channel(client, channel_id)


if __name__ == "__main__":
    main()
