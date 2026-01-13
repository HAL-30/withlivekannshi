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

@app.route('/')
def home():
    return "WithLIVE Monitor is running!"

@app.route('/health')
def health():
    return "OK", 200

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# ========== 設定チェック ==========
def validate_config(config):
    errors = []
    if not config.get("discord_webhook_url"):
        errors.append("DISCORD_WEBHOOK_URL が設定されていません")
    if not config.get("cast_id"):
        errors.append("CAST_ID が設定されていません")

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

    def __init__(self, config):
        self.config = config
        self.previous_slots = set()
        self.baseline_ready = False
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.consecutive_failures = 0

    def log(self, message):
        timestamp = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {message}", flush=True)

    def get_check_dates(self, days_ahead=2):
        dates = []
        today = datetime.now(JST)
        for i in range(days_ahead):
            date = today + timedelta(days=i)
            dates.append(date.strftime('%Y%m%d'))
        return dates

    def fetch_slots_for_date(self, date_str):
        """APIから指定日の枠情報を取得"""
        url = f"{self.API_BASE}/talk/cast/{self.config['cast_id']}?status=progress_draft&date={date_str}&is_web=true"
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            # APIレスポンスからslot情報を抽出
            result = data.get('result', [])
            slots = []
            for item in result:
                slot = {
                    'id': str(item.get('talk_id', '')),
                    'date': date_str,
                    'time': item.get('start_time', ''),
                    'points': str(item.get('point', item.get('current_price', 0))),
                    'start_day': item.get('start_day', ''),
                    'type': item.get('type', 1),  # 1=通常予約, 0=オークション
                    'is_published': item.get('is_published', 1),
                }
                slots.append(slot)

            return slots, True

        except Exception as e:
            self.log(f"❌ 取得エラー ({date_str}): {e}")
            return None, False

    def fetch_all_slots(self):
        dates = self.get_check_dates(self.config.get('days_ahead', 2))
        all_slots = []
        ok_all = True

        for date_str in dates:
            slots, ok = self.fetch_slots_for_date(date_str)
            if not ok:
                ok_all = False
            else:
                all_slots.extend(slots)
            time.sleep(0.5)

        return all_slots, ok_all

    def detect_new_slots_and_update(self, current_slots, ok_all):
        if not ok_all:
            self.consecutive_failures += 1
            self.log(f"⚠️ 取得失敗が含まれるため、状態更新をスキップ (連続{self.consecutive_failures}回)")
            if self.consecutive_failures == 10:
                self.send_error_notification("取得失敗が10回連続しています。APIの変更やブロックの可能性があります。")
            return []

        self.consecutive_failures = 0
        current_ids = {slot['id'] for slot in current_slots}
        new_ids = current_ids - self.previous_slots

        if not self.baseline_ready:
            self.previous_slots = current_ids
            self.baseline_ready = True
            self.log(f"✅ ベースライン確立 (既存枠: {len(self.previous_slots)}件)")
            return []

        self.previous_slots = current_ids

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

    def send_discord_notification(self, new_slots):
        if not new_slots:
            return
        embed = {
            "title": "🎉 電話枠が追加されました！",
            "description": f"**{self.config['artist_name']}**",
            "color": 0x00ff00,
            "fields": [],
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "WithLIVE Monitor"}
        }
        for slot in new_slots[:10]:
            # start_dayがあればそれを使用、なければdate_strからフォーマット
            date_display = slot.get('start_day') or self.format_date(slot['date'])
            time_display = slot.get('time', '')
            embed["fields"].append({
                "name": f"📅 {date_display} {time_display}",
                "value": f"💰 {slot['points']}pt",
                "inline": True
            })
        embed["fields"].append({
            "name": "📱 今すぐ予約",
            "value": f"[予約ページを開く](https://www.withlive.jp/cast.php?id={self.config['cast_id']})",
            "inline": False
        })
        payload = {"content": "@everyone 電話枠追加！", "embeds": [embed]}
        try:
            resp = requests.post(self.config['discord_webhook_url'], json=payload, timeout=10)
            resp.raise_for_status()
            self.log(f"✅ Discord通知送信完了 ({len(new_slots)}件)")
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
            requests.post(self.config['discord_webhook_url'], json=payload, timeout=10)
        except Exception:
            pass

    def send_test_notification(self):
        payload = {
            "content": "🔔 WithLIVE Monitor 起動しました",
            "embeds": [{
                "title": "✅ 監視開始",
                "description": f"**{self.config['artist_name']}** を監視中",
                "color": 0x0099ff,
                "fields": [
                    {"name": "チェック間隔", "value": f"{self.config['check_interval']}秒", "inline": True},
                    {"name": "監視日数", "value": f"{self.config.get('days_ahead', 2)}日分", "inline": True}
                ]
            }]
        }
        try:
            resp = requests.post(self.config['discord_webhook_url'], json=payload, timeout=10)
            resp.raise_for_status()
            self.log("✅ 起動通知送信完了")
            return True
        except Exception as e:
            self.log(f"❌ 起動通知エラー: {e}")
            return False

    def initialize(self):
        self.log("初期化中...")
        slots, ok_all = self.fetch_all_slots()
        if ok_all:
            self.previous_slots = {slot['id'] for slot in slots}
            self.baseline_ready = True
            self.log(f"✅ 初期化完了 (既存枠: {len(self.previous_slots)}件)")
        else:
            self.previous_slots = set()
            self.baseline_ready = False
            self.log("⚠️ 初期化に失敗。次の成功タイミングでベースラインを作ります。")

    def run(self):
        self.log("=== WithLIVE Monitor 起動 ===")
        self.log(f"対象: {self.config['artist_name']}")
        self.log(f"Cast ID: {self.config['cast_id']}")
        self.log(f"監視日数: {self.config.get('days_ahead', 2)}日分")

        if not self.send_test_notification():
            self.log("⚠️ Discord通知に失敗しました。Webhook URLを確認してください")

        self.initialize()
        self.log(f"監視開始 (間隔: {self.config['check_interval']}秒)")

        while True:
            try:
                time.sleep(self.config['check_interval'])
                current_slots, ok_all = self.fetch_all_slots()
                new_slots = self.detect_new_slots_and_update(current_slots, ok_all)
                if new_slots:
                    self.log(f"🎉 新規枠検出: {len(new_slots)}件")
                    self.send_discord_notification(new_slots)
                else:
                    self.log("チェック完了 - 新規枠なし")
            except Exception as e:
                self.log(f"❌ 予期しないエラー: {e}")
                time.sleep(10)

# ========== 設定 ==========
config = {
    "discord_webhook_url": os.environ.get("DISCORD_WEBHOOK_URL"),
    "cast_id": os.environ.get("CAST_ID"),
    "artist_name": os.environ.get("ARTIST_NAME", "アーティスト"),
    "check_interval": int(os.environ.get("CHECK_INTERVAL", 60)),
    "days_ahead": int(os.environ.get("DAYS_AHEAD", 2)),
}

# ========== 起動 ==========
if __name__ == "__main__":
    validate_config(config)
    web_thread = Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()
    monitor = WithLiveMonitor(config)
    monitor.run()
