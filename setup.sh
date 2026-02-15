#!/bin/bash
# ============================================================
# WithLIVE Monitor - OCI VM セットアップスクリプト
# 対象: Oracle Linux 9 / VM.Standard.E2.1.Micro
# 
# 使い方:
#   1. SSH接続: ssh -i ssh-key-2026-02-10.key opc@132.145.121.233
#   2. git clone https://github.com/HAL-30/withlivekannshi.git
#   3. cd withlivekannshi
#   4. chmod +x setup.sh
#   5. ./setup.sh
# ============================================================

set -e

echo "=============================="
echo " WithLIVE Monitor セットアップ"
echo "=============================="

# --- 1. システム確認 ---
echo ""
echo "[1/5] システム確認..."
echo "  OS: $(cat /etc/oracle-release 2>/dev/null || cat /etc/os-release | head -1)"
echo "  メモリ: $(free -h | awk '/^Mem:/{print $2}')"
echo "  Python: $(python3 --version 2>/dev/null || echo '未インストール')"

# --- 2. Python3 確認（Oracle Linux 9 はデフォルトで入っている） ---
echo ""
echo "[2/5] Python3 確認..."
if command -v python3 &> /dev/null; then
    echo "  Python3 は既にインストール済みです: $(python3 --version)"
else
    echo "  Python3 をインストールします..."
    sudo yum install -y python3
fi

# --- 3. 環境変数ファイル作成 ---
echo ""
echo "[3/5] 環境変数設定..."

ENV_FILE="/home/opc/withlivekannshi/.env"

if [ -f "$ENV_FILE" ]; then
    echo "  .env ファイルが既に存在します。上書きしますか？ (y/N)"
    read -r answer
    if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
        echo "  スキップしました"
    else
        CREATE_ENV=true
    fi
else
    CREATE_ENV=true
fi

if [ "$CREATE_ENV" = true ]; then
    echo "  Discord Webhook URL を入力してください:"
    read -r webhook_url
    
    if [ -z "$webhook_url" ]; then
        echo "  エラー: Webhook URL が空です"
        exit 1
    fi
    
    cat > "$ENV_FILE" << EOF
DISCORD_WEBHOOK_URL=${webhook_url}
DAYS_AHEAD=2
EOF
    
    chmod 600 "$ENV_FILE"
    echo "  .env ファイルを作成しました (パーミッション: 600)"
fi

# --- 4. テスト実行 ---
echo ""
echo "[4/5] テスト実行（5秒で停止）..."
echo "  環境変数を読み込んで起動テストします..."

set -a
source "$ENV_FILE"
set +a

timeout 5 python3 main.py || true
echo "  テスト完了"

# --- 5. systemd サービス登録 ---
echo ""
echo "[5/5] systemd サービス登録..."

sudo cp withlive-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable withlive-monitor
sudo systemctl start withlive-monitor

echo ""
echo "=============================="
echo " セットアップ完了！"
echo "=============================="
echo ""
echo "■ サービス状態確認:"
echo "  sudo systemctl status withlive-monitor"
echo ""
echo "■ ログ確認（リアルタイム）:"
echo "  sudo journalctl -u withlive-monitor -f"
echo ""
echo "■ ログ確認（最新50行）:"
echo "  sudo journalctl -u withlive-monitor -n 50"
echo ""
echo "■ サービス再起動:"
echo "  sudo systemctl restart withlive-monitor"
echo ""
echo "■ サービス停止:"
echo "  sudo systemctl stop withlive-monitor"
echo ""

sudo systemctl status withlive-monitor --no-pager
