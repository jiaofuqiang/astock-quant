#!/bin/bash
# 三刀流·周一开盘全链路自动化脚本
# 用法: bash scripts/monday_open_sequence.sh [--date=2026-05-18]
# 默认使用今天日期

set -e
BASE="/home/ubuntu/astock"
V2BOARD="/home/ubuntu/V2board"
DATE_ARG="${1:-}"
DT="${DATE_ARG#--date=}"

cd "$BASE"

echo "════════════════════════════════════════════"
echo " 三刀流·周一自动开盘 $([ -n "$DT" ] && echo "($DT)")"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════"

# ===== 步骤1: 矛盾引擎(环境评分) =====
echo ""
echo "┌─────────────────────────────────────────┐"
echo "│ 步骤1/4: 矛盾引擎环境评分                │"
echo "└─────────────────────────────────────────┘"
python3 scripts/contradiction_engine_v2.py 2>&1 | grep -v 'Traceback'
sleep 2

# ===== 步骤2: S级交易引擎 =====
echo ""
echo "┌─────────────────────────────────────────┐"
echo "│ 步骤2/4: S级交易引擎扫描                 │"
echo "└─────────────────────────────────────────┘"
if [ -n "$DT" ]; then
    python3 scripts/s_trade_engine.py --date="$DT" 2>&1
else
    python3 scripts/s_trade_engine.py 2>&1
fi
sleep 1

# ===== 步骤3: 聚合器打包 =====
echo ""
echo "┌─────────────────────────────────────────┐"
echo "│ 步骤3/4: 聚合器打包bundle                │"
echo "└─────────────────────────────────────────┘"
cd "$V2BOARD"
python3 dashboard_aggregator.py 2>&1 | head -5

# ===== 步骤4: 输出结果 =====
echo ""
echo "┌─────────────────────────────────────────┐"
echo "│ 步骤4/4: 输出决策                       │"
echo "└─────────────────────────────────────────┘"
BUNDLE="$V2BOARD/dashboard_bundle.json"
if [ -f "$BUNDLE" ]; then
    ENV=$(python3 -c "import json; d=json.load(open('$BUNDLE')); print(d.get('market_env',{}).get('tier','?'), d.get('market_env',{}).get('score','?'))")
    CANDIDATES=$(python3 -c "
import json; d=json.load(open('$BUNDLE'))
st = d.get('s_trade',{})
if st.get('signals'):
    for s in st['signals']:
        print(f\"  🚀 {s['name']}({s['code']}) {s['tier']}级 {s['strategy']} {s['pos']}%仓位\")
else:
    print('  无S级信号')
print(f\"\\n  环境: {d.get('market_env',{}).get('tier','?')} {d.get('market_env',{}).get('score',0)}分\")
")
    echo "  环境: $ENV"
    echo "  S级候选:"
    python3 -c "
import json; d=json.load(open('$BUNDLE'))
st = d.get('s_trade',{})
if st.get('signals'):
    for s in st['signals']:
        print(f\"  🚀 {s['name']}({s['code']}) {s['tier']}级 {s['strategy']} {s['pos']}%仓位\")
else:
    print('  无S级候选')
"
fi

echo ""
echo "════════════════════════════════════════════"
echo " ✅ 全链路完成"
echo " ⏰ $(date '+%H:%M:%S')"
echo " 📊 前端: http://localhost:80"
echo "════════════════════════════════════════════"
