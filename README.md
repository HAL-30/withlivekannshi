# WithLIVE Monitor

WithLIVEの電話枠（トーク枠）を監視し、新規枠が追加された際にDiscordへ通知するシステムです。

## 機能

- 1分間隔でWithLIVEの枠情報を取得
- 当日と翌日の2日分を監視
- 新規枠検出時にDiscord Webhookで通知
- システム起動時にDiscordへ起動完了を通知
- 連続10回の取得失敗時に警告通知

## 環境変数

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|------------|------|
| `DISCORD_WEBHOOK_URL` | ✅ | - | Discord Webhook URL |
| `CAST_ID` | ✅ | - | 監視対象アーティストのID |
| `ARTIST_NAME` | - | アーティスト | 通知に表示する名前 |
| `CHECK_INTERVAL` | - | 60 | チェック間隔（秒） |
| `DAYS_AHEAD` | - | 2 | 監視日数 |
| `PORT` | - | 8080 | Webサーバーポート |

## デプロイ方法

### Renderへのデプロイ

1. GitHubリポジトリを作成し、コードをプッシュ
2. [Render](https://render.com)にログイン
3. 「New +」→「Web Service」を選択
4. リポジトリを接続
5. 以下の設定を入力:
   - **Name**: `withlive-monitor`
   - **Region**: `Singapore (Southeast Asia)`
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: `Free`
6. 環境変数を設定
7. 「Create Web Service」をクリック

### UptimeRobotの設定（スリープ防止）

1. [UptimeRobot](https://uptimerobot.com)にアカウント作成
2. 「+ Add New Monitor」をクリック
3. 以下を設定:
   - **Monitor Type**: `HTTP(s)`
   - **URL**: `https://{your-app}.onrender.com/health`
   - **Monitoring Interval**: `5 minutes`

## Cast IDの確認方法

1. WithLIVEで対象アーティストのページを開く
2. URLを確認: `https://www.withlive.jp/cast.php?id=XXXXXXXX`
3. `id=` の後ろの文字列がCast ID

## ローカルでのテスト

```bash
# 環境変数を設定
export DISCORD_WEBHOOK_URL="your_webhook_url"
export CAST_ID="your_cast_id"
export ARTIST_NAME="アーティスト名"

# 依存パッケージをインストール
pip install -r requirements.txt

# 実行
python main.py
```

## ログ例

```
[2026-01-13 18:30:00] === WithLIVE Monitor 起動 ===
[2026-01-13 18:30:00] 対象: アーティスト名
[2026-01-13 18:30:00] Cast ID: iZyHajhNQtgiE59IwcUqMOrRasy1
[2026-01-13 18:30:00] 監視日数: 2日分
[2026-01-13 18:30:01] ✅ 起動通知送信完了
[2026-01-13 18:30:03] ✅ 初期化完了 (既存枠: 3件)
[2026-01-13 18:30:03] 監視開始 (間隔: 60秒)
[2026-01-13 18:31:05] チェック完了 - 新規枠なし
[2026-01-13 18:33:05] 🎉 新規枠検出: 2件
[2026-01-13 18:33:05] ✅ Discord通知送信完了 (2件)
```

## ライセンス

MIT License
