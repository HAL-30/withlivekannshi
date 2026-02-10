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

# ========== 監視クラス ==========
class WithLiveMonitor:
    API_BASE = "https://production-api.withlive.co.uk/v1"

    def __init__(self, discord_webhook_url, priority_artist, normal_artists, days_ahead=2):
        self.discord_webhook_url = discord_webhook_url
        self.priority_artist = priority_artist
        self.normal_artists = normal_artists
        self.days_ahead = days_ahead
        
        self.previous_slots = {}
        self.baseline_ready = {}
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

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
                    'duration': str(item.get('duration', '')),
                }
                slots.append(slot)

            return slots, True

        except Exception as e:
            self.log(f"取得エラー ({cast_id[:8]}...): {e}")
            return None, False

    def fetch_all_slots_for_artist(self, cast_id):
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

    def detect_new_slots(self, artist, current_slots, ok_all):
        cast_id = artist['cast_id']
        
        if cast_id not in self.previous_slots:
            self.previous_slots[cast_id] = set()
            self.baseline_ready[cast_id] = False

        if not ok_all:
            self.log(f"{artist['name']}: 取得失敗")
            return []

        current_ids = {slot['id'] for slot in current_slots}

        if not self.baseline_ready[cast_id]:
            self.previous_slots[cast_id] = current_ids
            self.baseline_ready[cast_id] = True
            self.log(f"{artist['name']}: ベースライン確立 (既存枠: {len(current_ids)}件)")
            return []

        new_ids = current_ids - self.previous_slots[cast_id]
        self.previous_slots[cast_id] = current_ids

        if not new_ids:
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
        
        try:
            resp = requests.post(self.discord_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            self.log(f"Discord通知送信完了: {artist['name']} ({len(new_slots)}件)")
        except Exception as e:
            self.log(f"Discord送信エラー: {e}")

    def send_startup_notification(self):
        all_artists = [self.priority_artist] + self.normal_artists
        artist_list = "\n".join([f"・{a['name']}" for a in all_artists])
        
        payload = {
            "content": "🔔 WithLIVE Monitor 起動しました",
            "embeds": [{
                "title": "監視開始",
                "description": f"**監視対象:**\n{artist_list}",
                "color": 0x0099ff,
            }]
        }
        
        try:
            resp = requests.post(self.discord_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            self.log("起動通知送信完了")
        except Exception as e:
            self.log(f"起動通知エラー: {e}")

    def check_artist(self, artist):
        slots, ok = self.fetch_all_slots_for_artist(artist['cast_id'])
        new_slots = self.detect_new_slots(artist, slots, ok)
        
        if new_slots:
            self.log(f"{artist['name']}: 新規枠検出 {len(new_slots)}件")
            self.send_discord_notification(artist, new_slots)
            return True
        return False

    def initialize(self):
        self.log("初期化中...")
        self.check_artist(self.priority_artist)
        for artist in self.normal_artists:
            self.check_artist(artist)
            time.sleep(0.5)
        self.log("初期化完了")

    def run(self):
        self.log("=== WithLIVE Monitor 起動 ===")
        self.send_startup_notification()
        self.initialize()
        self.log("監視開始")

        cycle = 0
        while True:
            try:
                time.sleep(30)
                cycle += 1
                
                found = self.check_artist(self.priority_artist)
                if not found:
                    self.log(f"{self.priority_artist['name']}: 新規枠なし")
                
                if cycle % 2 == 0:
                    for artist in self.normal_artists:
                        found = self.check_artist(artist)
                        if not found:
                            self.log(f"{artist['name']}: 新規枠なし")
                        time.sleep(0.5)
                        
            except Exception as e:
                self.log(f"エラー: {e}")
                time.sleep(10)

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
    
    web_thread = Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()
    
    monitor = WithLiveMonitor(
        discord_webhook_url=discord_webhook_url,
        priority_artist=PRIORITY_ARTIST,
        normal_artists=NORMAL_ARTISTS,
        days_ahead=days_ahead
    )
    monitor.run()
