import requests
import time
import os
import sys
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask

# ========== JST ==========
try:
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9))

# ========== Flaskサーバー ==========
app = Flask(__name__)
monitor_instance = None  # グローバル参照用

@app.route('/')
def home():
    return "WithLIVE Monitor is running!"

@app.route('/health')
def health():
    return "OK", 200

@app.route('/test')
def test_notification():
    """テスト通知を送信するエンドポイント"""
    global monitor_instance
    if monitor_instance is None:
        return "Monitor not initialized", 500
    
    # 優先アーティストでテスト通知を送信
    artist = monitor_instance.priority_artist
    test_slots = [
        {
            'id': 'test_001',
            'date': datetime.now(JST).strftime('%Y%m%d'),
            'time': '12:00:00',
            'points': '3000',
            'start_day': datetime.now(JST).strftime('%m/%d'),
        },
        {
            'id': 'test_002',
            'date': datetime.now(JST).strftime('%Y%m%d'),
            'time': '15:30:00',
            'points': '5000',
            'start_day': datetime.now(JST).strftime('%m/%d'),
        }
    ]
    
    try:
        monitor_instance.send_discord_notification(artist, test_slots)
        return f"✅ テスト通知を送信しました！（{artist['name']}）Discordを確認してください。", 200
    except Exception as e:
        return f"❌ エラー: {e}", 500

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# ========== 設定チェック ==========
def validate_config():
    errors = []
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        errors.append("DISCORD_WEBHOOK_URL が設定されていません")

    if errors:
        print("=" * 50, flush=True)
        print("❌ 設定エラー", flush=True)
        for e in errors:
            print(f"  - {e}", flush=True)
        print("=" * 50, flush=True)
        print("Renderの Environment Variables を確認してください", flush=True)
        sys.exit(1)

# ========== 監視クラス ==========
class WithLiveMonitor:
    API_BASE = "https://production-api.withlive.co.uk/v1"

    def __init__(self, discord_webhook_url, priority_artist, normal_artists, days_ahead=2):
        self.discord_webhook_url = discord_webhook_url
        self.priority_artist = priority_artist  # 30秒間隔
        self.normal_artists = normal_artists    # 60秒間隔
        self.days_ahead = days_ahead
        
        # 各アーティストの前回の枠IDを保持
        self.previous_slots = {}  # {cast_id: set of slot_ids}
        self.baseline_ready = {}  # {cast_id: bool}
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.consecutive_failures = {}  # {cast_id: int}

    def log(self, message):
        timestamp = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {message}", flush=True)

    def get_check_dates(self):
        dates = []
        today = datetime.now(JST)
        for i in range(self.days_ahead):
            date = today + timedelta(days=i)
            dates.append(date.strftime('%Y%m%d'))
        return dates

    def fetch_slots_for_date(self, cast_id, date_str):
        """APIから指定日の枠情報を取得"""
        url = f"{self.API_BASE}/talk/cast/{cast_id}?status=progress_draft&date={date_str}&is_web=true"
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            result = data.get('result', [])
            slots = []
            for item in result:
                slot = {
                    'id': str(item.get('talk_id', '')),
                    'date': date_str,
                    'time': item.get('start_time', ''),
                    'points': str(item.get('point', item.get('current_price', 0))),
                    'start_day': item.get('start_day', ''),
                    'type': item.get('type', 1),
                    'is_published': item.get('is_published', 1),
                }
                slots.append(slot)

            return slots, True

        except Exception as e:
            self.log(f"❌ 取得エラー ({cast_id[:8]}... / {date_str}): {e}")
            return None, False

    def fetch_all_slots_for_artist(self, cast_id):
        """指定アーティストの全日程の枠を取得"""
        dates = self.get_check_dates()
        all_slots = []
        ok_all = True

        for date_str in dates:
            slots, ok = self.fetch_slots_for_date(cast_id, date_str)
            if not ok:
                ok_all = False
            else:
                all_slots.extend(slots)
            time.sleep(0.3)

        return all_slots, ok_all

    def detect_new_slots_and_update(self, artist, current_slots, ok_all):
        """新規枠を検出し、状態を更新"""
        cast_id = artist['cast_id']
        
        if cast_id not in self.previous_slots:
            self.previous_slots[cast_id] = set()
            self.baseline_ready[cast_id] = False
            self.consecutive_failures[cast_id] = 0

        if not ok_all:
            self.consecutive_failures[cast_id] += 1
            self.log(f"⚠️ {artist['name']}: 取得失敗 (連続{self.consecutive_failures[cast_id]}回)")
            if self.consecutive_failures[cast_id] == 10:
                self.send_error_notification(f"{artist['name']}の取得失敗が10回連続しています。")
            return []

        self.consecutive_failures[cast_id] = 0
        current_ids = {slot['id'] for slot in current_slots}
        new_ids = current_ids - self.previous_slots[cast_id]

        if not self.baseline_ready[cast_id]:
            self.previous_slots[cast_id] = current_ids
            self.baseline_ready[cast_id] = True
            self.log(f"✅ {artist['name']}: ベースライン確立 (既存枠: {len(current_ids)}件)")
            return []

        self.previous_slots[cast_id] = current_ids

        if not new_ids:
            return []

        by_id = {slot['id']: slot for slot in current_slots}
        return [by_id[sid] for sid in new_ids if sid in by_id]

    def format_date(self, date_str):
        try:
            dt = datetime.strptime(date_str, '%Y%m%d')
            weekdays = ['月', '火', '水', '木', '金', '土', '日']
            return f"{dt.month}/{dt.day}({weekdays[dt.weekday()]})"
        except Exception:
            return date_str

    def format_time(self, time_str):
        """start_timeから時刻部分のみを抽出してJSTに変換"""
        try:
            # "2026-01-18 16:50:00" 形式の場合
            if ' ' in time_str:
                dt_utc = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                # UTCからJSTに変換（+9時間）
                dt_jst = dt_utc + timedelta(hours=9)
                return dt_jst.strftime('%H:%M:%S')
            # すでに "01:50:00" 形式の場合はそのまま
            return time_str
        except Exception:
            return time_str

    def send_discord_notification(self, artist, new_slots):
        if not new_slots:
            return
        embed = {
            "title": "🎉 電話がきたよ！",
            "description": f"\n**{artist['name']}**\n",
            "color": 0x00ff00,
            "fields": [],
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "WithLIVE Monitor"}
        }
        for slot in new_slots[:10]:
            date_display = slot.get('start_day') or self.format_date(slot['date'])
            time_display = self.format_time(slot.get('time', ''))
            embed["fields"].append({
                "name": f"📅 {date_display} {time_display}",
                "value": f"💰 {slot['points']}pt\n",
                "inline": True
            })
        embed["fields"].append({
            "name": "\u200b",
            "value": f"📱 [今すぐ予約する](https://www.withlive.jp/cast.php?id={artist['cast_id']})",
            "inline": False
        })
        
        # アーティストごとのメッセージ
        message = artist.get('message', f"{artist['name']}から着信がありました！")
        payload = {"content": message, "embeds": [embed]}
        
        try:
            resp = requests.post(self.discord_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            self.log(f"✅ Discord通知送信完了: {artist['name']} ({len(new_slots)}件)")
        except Exception as e:
            self.log(f"❌ Discord送信エラー: {e}")

    def send_error_notification(self, message):
        payload = {
            "embeds": [{
                "title": "⚠️ WithLIVE Monitor 警告",
                "description": message,
                "color": 0xff0000,
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        try:
            requests.post(self.discord_webhook_url, json=payload, timeout=10)
        except Exception:
            pass

    def send_startup_notification(self):
        all_artists = [self.priority_artist] + self.normal_artists
        artist_list = "\n".join([f"・{a['name']}" for a in all_artists])
        
        payload = {
            "content": "🔔 WithLIVE Monitor 起動しました",
            "embeds": [{
                "title": "✅ 監視開始",
                "description": f"**監視対象アーティスト:**\n{artist_list}",
                "color": 0x0099ff,
                "fields": [
                    {"name": "優先監視", "value": f"{self.priority_artist['name']} (30秒間隔)", "inline": True},
                    {"name": "通常監視", "value": f"{len(self.normal_artists)}人 (60秒間隔)", "inline": True}
                ]
            }]
        }
        try:
            resp = requests.post(self.discord_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            self.log("✅ 起動通知送信完了")
            return True
        except Exception as e:
            self.log(f"❌ 起動通知エラー: {e}")
            return False

    def check_artist(self, artist):
        """指定アーティストをチェックし、新規枠があれば通知"""
        slots, ok = self.fetch_all_slots_for_artist(artist['cast_id'])
        new_slots = self.detect_new_slots_and_update(artist, slots, ok)
        
        if new_slots:
            self.log(f"🎉 {artist['name']}: 新規枠検出 {len(new_slots)}件")
            self.send_discord_notification(artist, new_slots)
            return True
        return False

    def initialize(self):
        """全アーティストの初期状態を取得"""
        self.log("初期化中...")
        
        # 優先アーティスト
        self.check_artist(self.priority_artist)
        
        # 通常アーティスト
        for artist in self.normal_artists:
            self.check_artist(artist)
            time.sleep(0.5)
        
        self.log("✅ 初期化完了")

    def run(self):
        self.log("=== WithLIVE Monitor 起動 ===")
        self.log(f"優先監視 (30秒): {self.priority_artist['name']}")
        for artist in self.normal_artists:
            self.log(f"通常監視 (60秒): {artist['name']}")

        if not self.send_startup_notification():
            self.log("⚠️ Discord通知に失敗しました。Webhook URLを確認してください")

        self.initialize()
        self.log("監視開始")

        cycle = 0
        while True:
            try:
                time.sleep(30)
                cycle += 1
                
                # 優先アーティストは毎回（30秒ごと）
                found = self.check_artist(self.priority_artist)
                if not found:
                    self.log(f"{self.priority_artist['name']}: 新規枠なし")
                
                # 通常アーティストは2回に1回（60秒ごと）
                if cycle % 2 == 0:
                    for artist in self.normal_artists:
                        found = self.check_artist(artist)
                        if not found:
                            self.log(f"{artist['name']}: 新規枠なし")
                        time.sleep(0.5)
                        
            except Exception as e:
                self.log(f"❌ 予期しないエラー: {e}")
                time.sleep(10)

# ========== アーティスト設定 ==========
# 優先監視（30秒間隔）
PRIORITY_ARTIST = {
    "cast_id": "iZyHajhNQtgiE59IwcUqMOrRasy1",
    "name": "澪・モンスター",
    "message": "みおちゃんから着信がありました！"
}

# 通常監視（60秒間隔）
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
    validate_config()
    
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    days_ahead = int(os.environ.get("DAYS_AHEAD", 2))
    
    web_thread = Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()
    
    monitor = WithLiveMonitor(
        discord_webhook_url=discord_webhook_url,
        priority_artist=PRIORITY_ARTIST,
        normal_artists=NORMAL_ARTISTS,
        days_ahead=days_ahead
    )
    monitor_instance = monitor
    monitor.run()
