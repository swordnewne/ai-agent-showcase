#!/bin/bash
# 聚宽模拟盘 每日市值更新流程
# 用法: ./daily_portfolio_update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/../data"
PRICE_FILE="$DATA_DIR/prices.csv"

# 获取持仓中的股票代码
SYMBOLS=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DATA_DIR/finance.db')
cursor = conn.cursor()
cursor.execute('SELECT DISTINCT symbol FROM portfolio_events WHERE event_type = \"position\" AND event_date = (SELECT MAX(event_date) FROM portfolio_events WHERE event_type = \"position\")')
symbols = [row[0] for row in cursor.fetchall()]
print(','.join(symbols))
conn.close()
")

echo "=== 获取实时价格 ==="
echo "股票: $SYMBOLS"

# 使用 kimi_finance 获取价格（需要 OpenClaw 环境）
# 如果 kimi_finance 不可用，则使用缓存
if command -v kimi_finance &> /dev/null; then
    kimi_finance --ticker "$SYMBOLS" --type realtime_price --file_path "$PRICE_FILE"
else
    echo "注意: kimi_finance 不可用，使用缓存或手动获取价格"
    echo "请手动运行: kimi_finance --ticker '$SYMBOLS' --type realtime_price --file_path '$PRICE_FILE'"
    
    # 检查是否有缓存
    if [ ! -f "$PRICE_FILE" ]; then
        echo "错误: 价格缓存不存在，请先获取价格"
        exit 1
    fi
fi

echo ""
echo "=== 计算市值 ==="
python3 "$SCRIPT_DIR/update_portfolio_value.py"

echo ""
echo "=== 完成 ==="
