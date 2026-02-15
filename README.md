# WithLIVE Monitor

WithLIVEの電話枠（トーク枠）を監視し、新規枠が追加された際にDiscordへ通知するシステムです。

## 動作環境

| 項目 | 値 |
|------|-----|
| サーバー | OCI VM.Standard.E2.1.Micro (Always Free) |
| OS | Oracle Linux 9 |
| リージョン | AP-TOKYO-1 |
| メモリ使用量 | 約20-30MB |
| 外部依存 | なし（Python3 標準ライブラリのみ） |

## 機能

- 澪・モンスターを**30秒間隔**で優先監視
- その他アーティストを**60秒間隔**で監視
- 当日と翌日の2日分を監視
- 新規枠検出時にDiscord Webhookで通知
- systemdによる24時間自動稼働・自動再起動
- graceful shutdown対応

## 監視対象アーティスト

| アーティスト | 監視間隔 | 通知メッセージ |
|-------------|----------|---------------|
| 澪・モンスター | 30秒 | みおちゃんから着信がありました！ |
| あぐりぴこ | 60秒 | あぐりぴこから着信がありました！ |
| 渋谷龍華 | 60秒 | 渋谷龍華から着信がありました！ |
| 卯月彩華 | 60秒 | 卯月彩華から着信がありました！ |

## OCI VMへのデプロイ

### 前提条件

- OCI VM.Standard.E2.1.Micro が起動済み
- SSH秘密鍵 `ssh-key-2026-02-10.key` がある
- Discord Webhook URLを用意する

### 手順

```bash
# 1. SSH接続（Macのターミナルから）
cd ~/Downloads
chmod 400 ssh-key-2026-02-10.key
ssh -i ssh-key-2026-02-10.key opc@132.145.121.233

# 2. リポジトリをクローン（VM内で実行）
git clone https://github.com/HAL-30/withlivekannshi.git
cd withlivekannshi

# 3. セットアップスクリプトを実行
chmod +x setup.sh
./setup.sh
# → Discord Webhook URL の入力を求められます
```

これだけで完了です！自動的に：
- `.env` ファイルが作成されます
- テスト実行されます
- systemdサービスとして登録・起動されます

## よく使うコマンド（SSH接続後）

```bash
# サービスの状態確認
sudo systemctl status withlive-monitor

# ログをリアルタイムで見る
sudo journalctl -u withlive-monitor -f

# 最新50行のログ
sudo journalctl -u withlive-monitor -n 50

# サービス再起動
sudo systemctl restart withlive-monitor

# サービス停止
sudo systemctl stop withlive-monitor

# Webhook URLの変更
nano ~/withlivekannshi/.env
sudo systemctl restart withlive-monitor

# コードの更新
cd ~/withlivekannshi
git pull
sudo systemctl restart withlive-monitor
```

## 環境変数

`.env` ファイルで管理されます（setup.sh が自動作成）

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|------------|------|
| `DISCORD_WEBHOOK_URL` | ✅ | - | Discord Webhook URL |
| `DAYS_AHEAD` | - | 2 | 監視日数（当日+翌日） |

## ファイル構成

```
withlivekannshi/
├── main.py                    # メインプログラム
├── requirements.txt           # 依存ライブラリ（なし）
├── withlive-monitor.service   # systemdサービス定義
├── setup.sh                   # 自動セットアップスクリプト
├── render.yaml                # Render設定（非推奨）
├── .env                       # 環境変数（git管理外）
├── .gitignore
└── README.md
```

## 旧Render版からの変更点

| 項目 | Render版 | OCI Micro版 |
|------|----------|-------------|
| Flask | あり（UptimeRobot用） | **削除**（常時稼働のため不要） |
| requests | 使用 | **削除**（urllib標準に置換） |
| beautifulsoup4 | 不使用だが記載 | **削除** |
| メモリ使用量 | 約80-100MB | **約20-30MB** |
| プロセス管理 | なし | **systemd**（自動再起動） |
| メモリ保護 | なし | **MemoryMax=200M** |
| shutdown | なし | **graceful shutdown** |

## トラブルシューティング

### VMが「Stopping」でフリーズした場合

1. OCIコンソールでブラウザを更新
2. 5-10分待つ
3. Actions → Stop → **Force stop the instance** にチェック → Stop
4. Stoppedになったら Actions → Start

### Discord通知が来ない場合

```bash
# ログを確認
sudo journalctl -u withlive-monitor -n 100

# Webhook URLの確認
cat ~/withlivekannshi/.env

# 手動テスト（5秒で停止）
cd ~/withlivekannshi
source .env && timeout 5 python3 main.py
```

### メモリ不足の場合

```bash
# メモリ使用状況
free -h

# スワップの追加（必要な場合）
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

## ライセンス

MIT License
