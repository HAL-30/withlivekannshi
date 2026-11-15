#!/usr/bin/env python3
"""
WithLIVE 電話枠監視システム (OCI Micro 最適化版)

OCI VM.Standard.E2.1.Micro (1GB RAM) 向けに最適化
- Flask削除（常時稼働VMなのでUptimeRobot不要）
- 最小メモリフットプリント（約20-30MB）
- systemd連携（ログはjournaldへ）
- graceful shutdown対応
"""

import json
import signal
import sys
import time
import os
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

# ========== JST ==========
try:
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9))

# ========== Graceful Shutdown ==========
running = True

def shutdown_handler(signum, frame):
    global running
    log("シャットダウンシグナル受信")
    running = False

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# ========== ログ ==========
def log(message):
    timestamp = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", flush=True)

# ========== HTTP ヘルパー (urllib のみ、requests不要) ==========
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

def http_get_json(url, timeout=30):
    """URLからJSONを取得（標準ライブラリのみ）"""
    req = Request(url, headers={'User-Agent': USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

def http_post_json(url, data, timeout=10):
    """URLにJSONをPOST（標準ライブラリのみ）"""
    payload = json.dumps(data).encode('utf-8')
    req = Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, True
    except HTTPError as e:
        log(f"HTTP POST エラー: {e.code} {e.reason}")
        return e.code, False
    except (URLError, TimeoutError) as e:
        log(f"HTTP POST 接続エラー: {e}")
        return 0, False

# ========== 監視クラス ==========
class WithLiveMonitor:
    API_BASE = "https://production-api.withlive.co.uk/v1"

    def __init__(self, discord_webhook_url, priority_artist, normal_artists, days_ahead=2):
        self.discord_webhook_url = discord_webhook_url
        self.priority_artist = priority_artist
        self.normal_artists = normal_artists
        self.days_ahead = days_ahead

        # 各アーティストの既知スロットID
        self.previous_slots = {}

    def get_check_dates(self):
        today = datetime.now(JST)
        return [(today + timedelta(days=i)).strftime('%Y%m%d') for i in range(self.days_ahead)]

    def fetch_slots_for_date(self, cast_id, date_str):
        url = f"{self.API_BASE}/talk/cast/{cast_id}?status=progress_draft&date={date_str}&is_web=true"
        data = http_get_json(url)
        items = data.get('result') if isinstance(data, dict) else None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("枠一覧のレスポンス形式が不正です")

        slots = []
        for item in items:
            slots.append({
                'id': str(item.get('talk_id', '')),
                'date': date_str,
                'time': item.get('start_time', ''),
                'points': str(item.get('point', item.get('current_price', 0))),
                'start_day': item.get('start_day', ''),
                'duration': str(item.get('duration', '')),
            })
        return slots

    def fetch_all_slots_for_artist(self, artist):
        dates = self.get_check_dates()
        all_slots = []
        ok_all = True

        for date_str in dates:
            try:
                all_slots.extend(self.fetch_slots_for_date(artist['cast_id'], date_str))
            except (URLError, TimeoutError, ValueError) as error:
                ok_all = False
                detail = str(error)
                if isinstance(error, HTTPError):
                    detail = f"HTTP {error.code} {error.reason}"
                log(f"{artist['name']}: 取得失敗 ({date_str}): {detail}")
            time.sleep(0.3)

        return all_slots, ok_all

    def detect_new_slots(self, artist, current_slots):
        cast_id = artist['cast_id']
        current_ids = {slot['id'] for slot in current_slots}

        if cast_id not in self.previous_slots:
            self.previous_slots[cast_id] = current_ids
            log(f"{artist['name']}: ベースライン確立 (既存枠: {len(current_ids)}件)")
            return []

        new_ids = current_ids - self.previous_slots[cast_id]
        self.previous_slots[cast_id] = current_ids

        if not new_ids:
            log(f"{artist['name']}: 新規枠なし")
            return []

        by_id = {slot['id']: slot for slot in current_slots}
        return [by_id[sid] for sid in new_ids if sid in by_id]

    def format_date(self, date_str):
        try:
            dt = datetime.strptime(date_str, '%Y%m%d')
            return f"{dt.month}月{dt.day}日"
        except Exception:
            return date_str

    def format_start_day(self, start_day):
        if not start_day:
            return ""
        try:
            if '/' in start_day and '(' in start_day:
                date_part = start_day.split('(')[0]
                month, day = date_part.split('/')
                return f"{month}月{day}日"
            return start_day
        except Exception:
            return start_day

    def format_time(self, time_str):
        try:
            if ' ' in time_str:
                dt_utc = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                dt_jst = dt_utc + timedelta(hours=9)
                return dt_jst.strftime('%H:%M:%S')
            return time_str
        except Exception:
            return time_str

    def send_discord_notification(self, artist, new_slots):
        if not new_slots:
            return

        embed = {
            "title": "🎉 電話がきたよ！",
            "description": f"**{artist['name']}**",
            "color": 0x00ff00,
            "fields": [],
            "footer": {"text": "WithLIVE Monitor"}
        }

        for slot in new_slots[:10]:
            start_day = slot.get('start_day', '')
            date_display = self.format_start_day(start_day) if start_day else self.format_date(slot['date'])
            time_display = self.format_time(slot.get('time', ''))
            duration = slot.get('duration', '')
            duration_text = f" 〜 {duration}分" if duration else ""

            embed["fields"].append({
                "name": f"📅 {date_display}",
                "value": f"**{time_display}{duration_text}**",
                "inline": False
            })
            embed["fields"].append({
                "name": f"💰 {slot['points']}pt",
                "value": "━━━━━━━━━━━━━━━",
                "inline": False
            })

        embed["fields"].append({
            "name": "📱 今すぐ予約する",
            "value": f"[WithLIVEで予約](https://www.withlive.jp/cast.php?id={artist['cast_id']})",
            "inline": False
        })

        message = artist.get('message', f"{artist['name']}から着信がありました！")
        payload = {"content": message, "embeds": [embed]}

        status, ok = http_post_json(self.discord_webhook_url, payload)
        if ok:
            log(f"Discord通知送信完了: {artist['name']} ({len(new_slots)}件)")
        else:
            log(f"Discord送信エラー: ステータス {status}")

    def send_startup_notification(self):
        all_artists = [self.priority_artist] + self.normal_artists
        artist_list = "\n".join([f"・{a['name']}" for a in all_artists])

        payload = {
            "content": "🔔 WithLIVE Monitor 起動しました",
            "embeds": [{
                "title": "監視開始",
                "description": f"**監視対象:**\n{artist_list}\n\n**サーバー:** OCI Tokyo",
                "color": 0x0099ff,
            }]
        }

        status, ok = http_post_json(self.discord_webhook_url, payload)
        if ok:
            log("起動通知送信完了")
        else:
            log(f"起動通知エラー: ステータス {status}")

    def check_artist(self, artist):
        slots, ok = self.fetch_all_slots_for_artist(artist)
        if not ok:
            return

        new_slots = self.detect_new_slots(artist, slots)
        if new_slots:
            log(f"{artist['name']}: 新規枠検出 {len(new_slots)}件")
            self.send_discord_notification(artist, new_slots)

    def initialize(self):
        log("初期化中...")
        self.check_artist(self.priority_artist)
        for artist in self.normal_artists:
            self.check_artist(artist)
            time.sleep(0.5)
        log("初期化完了")

    def run(self):
        global running

        log("=== WithLIVE Monitor 起動 (OCI Micro版) ===")
        self.send_startup_notification()
        self.initialize()
        log("監視開始")

        cycle = 0
        while running:
            # 30秒待機（1秒刻みでシャットダウンチェック）
            for _ in range(30):
                if not running:
                    break
                time.sleep(1)

            if not running:
                break

            cycle += 1

            try:
                # 澪・モンスター: 毎サイクル（30秒間隔）
                self.check_artist(self.priority_artist)

                # その他: 2サイクルごと（60秒間隔）
                if cycle % 2 == 0:
                    for artist in self.normal_artists:
                        if not running:
                            break
                        self.check_artist(artist)
                        time.sleep(0.5)

            except Exception as e:
                log(f"エラー: {e}")
                time.sleep(10)

        log("=== WithLIVE Monitor 停止 ===")


# ========== アーティスト設定 ==========
PRIORITY_ARTIST = {
    "cast_id": "iZyHajhNQtgiE59IwcUqMOrRasy1",
    "name": "澪・モンスター",
    "message": "みおちゃんから着信がありました！"
}

NORMAL_ARTISTS = [
    {
        "cast_id": "zasSY7lrDAWXT3xw6Xm76j8JK1u1",
        "name": "あぐりぴこ",
        "message": "あぐりぴこから着信がありました！"
    },
    {
        "cast_id": "GAQJqxoWIASIuqPMshqbDOIrzPC3",
        "name": "渋谷龍華",
        "message": "渋谷龍華から着信がありました！"
    },
    {
        "cast_id": "zAKEiqid64c5DJinwtSr7mIb8p32",
        "name": "卯月彩華",
        "message": "卯月彩華から着信がありました！"
    },
]

# ========== 起動 ==========
if __name__ == "__main__":
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not discord_webhook_url:
        print("エラー: DISCORD_WEBHOOK_URL が設定されていません", flush=True)
        sys.exit(1)

    days_ahead = int(os.environ.get("DAYS_AHEAD", 2))

    monitor = WithLiveMonitor(
        discord_webhook_url=discord_webhook_url,
        priority_artist=PRIORITY_ARTIST,
        normal_artists=NORMAL_ARTISTS,
        days_ahead=days_ahead
    )
    monitor.run()
