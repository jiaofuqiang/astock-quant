#!/usr/bin/env python3
"""
扩展v2.json策略维度 — 基于严格时间线回测数据(regression_flow_engine SQL)
从lhb_detail中提取各种资金标签组合的T+1收益
"""
import sqlite3, json, os, math
from collections import defaultdict

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "astock")
LHB_DB = os.path.join(BASE, "data", "lhb_cache.db")
KLINE_DB = os.path.join(BASE, "data", "kline_cache.db")
V2_PATH = os.path.join(BASE, "data", "lhb_practical_backtest_v2.json")

conn = sqlite3.connect(LHB_DB)
conn_k = sqlite3.connect(KLINE_DB)

# ===== 1. 读取严格时间线回测数据（与regression_flow_engine同一SQL）=====
query = """
SELECT 
  l.date AS select_date, l.code, l.name, l.type, l.chg,
  k_t.open AS buy_price,
  k_t1.date AS sell_date,
  k_t1.close AS sell_price,
  k_t1.open AS t1_open,
  k_t.volume AS t_volume,
  k_t.high AS t_high,
  k_t.low AS t_low
FROM lhb_list l
JOIN kline k_t ON l.code = k_t.code AND k_t.date = DATE(l.date, '+1 day')
JOIN kline k_t1 ON l.code = k_t1.code AND k_t1.date = DATE(l.date, '+2 day')
WHERE l.type = '01'
  AND (l.code LIKE '6%' OR l.code LIKE '000%' OR l.code LIKE '001%' OR l.code LIKE '002%' OR l.code LIKE '003%')
  AND k_t.open IS NOT NULL AND k_t1.close IS NOT NULL
ORDER BY l.date DESC
"""
rows = conn.execute(query).fetchall()
print(f"基础数据: {len(rows)}笔交易")

# ===== 2. 获取每笔交易的资金标签 =====
detail_rows = conn.execute("""
    SELECT l.date, l.code, d.dealer, d.direction, d.buy_amt, d.sell_amt
    FROM lhb_list l
    JOIN lhb_detail d ON l.date = d.date AND l.code = d.code
    WHERE l.type = '01'
""").fetchall()

# 构建 {date_code: {标签集}}
trade_tags = defaultdict(set)
for row in detail_rows:
    d, c, dealer, direction, buy_amt, sell_amt = row
    key = f"{d}_{c}"
    dealer_str = dealer or ''
    net = (buy_amt or 0) - (sell_amt or 0)
    
    if direction == 'buy' and (buy_amt or 0) > 0:
        if '机构专用' in dealer_str:
            trade_tags[key].add('机构')
        if '量化' in dealer_str:
            trade_tags[key].add('量化')
        if any(yz in dealer_str for yz in ['炒股养家', '消闲派', '温州帮']):
            trade_tags[key].add('游资')
        if '沪股通' in dealer_str:
            trade_tags[key].add('沪股通')
        if '深股通' in dealer_str:
            trade_tags[key].add('深股通')

# ===== 3. 计算各标签组合的T+1收益 =====
WIN_PATTERNS = ['天童南路', '新闸路', '太平南路', '大连黄河路',
                 '北一环路', '江苏路', '中关村大街', '桑田路']
LOSE_PATTERNS = ['拉萨团结路', '拉萨东环路', '金融城南环路',
                  '拉萨东城区', '香曲东路']

# 为每笔交易打标签
tagged_trades = defaultdict(list)
win_seat_trades = []
lose_seat_trades = []

for row in rows:
    select_date, code, name, l_type, chg = row[0:5]
    buy_price = row[5]
    sell_date = row[6]
    sell_price = row[7]
    t1_open = row[8]
    t_volume = row[9]
    
    if not buy_price or not sell_price or buy_price <= 0:
        continue
    
    t1_close_ret = round((sell_price - buy_price) / buy_price * 100, 2)
    t1_open_ret = round((t1_open - buy_price) / buy_price * 100, 2) if t1_open else 0
    is_win = t1_close_ret > 0
    
    key = f"{select_date}_{code}"
    tags = trade_tags.get(key, set())
    
    # 交易日计算
    try:
        from datetime import datetime
        dt = datetime.strptime(select_date, '%Y-%m-%d')
        weekday = dt.weekday()
    except:
        weekday = -1
    
    # 按环境分类（从env_history）
    # 暂不计算环境，直接输出
    
    # 标签组合统计
    tag_str = ','.join(sorted(tags)) if tags else '无标签'
    
    trade = {
        'close_ret': t1_close_ret, 'open_ret': t1_open_ret, 
        'is_win': is_win, 'weekday': weekday,
        'code': code, 'date': select_date, 'buy_price': buy_price
    }
    tagged_trades[tag_str].append(trade)
    
    # 赚钱/亏钱席位
    dealer_buyers = []
    for rd in detail_rows:
        if f"{rd[0]}_{rd[1]}" == key and rd[3] == 'buy':
            dealer_buyers.append(rd[2] or '')
    has_win = any(any(p in d for p in WIN_PATTERNS) for d in dealer_buyers)
    has_lose = any(any(p in d for p in LOSE_PATTERNS) for d in dealer_buyers)
    
    if has_win:
        win_seat_trades.append(trade)
    if has_lose:
        lose_seat_trades.append(trade)

# ===== 4. 计算统计 =====
def calc_stats(trades_list, label=""):
    n = len(trades_list)
    if n == 0:
        return None
    close_rets = [t['close_ret'] for t in trades_list]
    open_rets = [t['open_ret'] for t in trades_list]
    wins = [t for t in trades_list if t['is_win']]
    big_wins = [r for r in close_rets if r >= 5]
    big_losses = [r for r in close_rets if r <= -5]
    return {
        'name': label,
        'n': n,
        'close_ret': round(sum(close_rets) / n, 2),
        'close_win': round(len(wins) / n * 100, 1),
        'open_ret': round(sum(open_rets) / n, 2),
        'open_win': round(sum(1 for r in open_rets if r > 0) / n * 100, 1) if n > 0 else 0,
        'big_win_pct': round(len(big_wins) / n * 100, 1),
        'big_loss_pct': round(len(big_losses) / n * 100, 1),
    }

# ===== 5. 生成策略列表 =====
strategies = []

# 5a. 按标签组合
for tag_str, trades in sorted(tagged_trades.items()):
    st = calc_stats(trades, f"{'无标签' if tag_str=='无标签' else tag_str}")
    if st and st['n'] >= 10:
        name = st['name']
        # 美化名称
        if name == '无标签':
            name = '无特殊资金标签'
        elif name == '机构':
            name = '🏛️机构买入'
        elif name == '沪股通':
            name = '🛂沪股通买入'
        elif name == '深股通':
            name = '🔵深股通买入'
        elif name == '量化':
            name = '🤖量化买入'
        elif name == '游资':
            name = '🎭游资买入'
        elif name == '机构,量化':
            name = '🏛️机构+🤖量化'
        elif name == '机构,游资':
            name = '🏛️机构+🎭游资'
        elif name == '量化,游资':
            name = '🤖量化+🎭游资'
        elif name == '机构,量化,游资':
            name = '🏛️三资金合力'
        elif name == '沪股通,机构':
            name = '🛂沪股通+🏛️机构'
        elif name == '深股通,机构':
            name = '🔵深股通+🏛️机构'
        else:
            name = f'标签组合:{name}'
        
        st['name'] = name
        st['desc'] = f"龙虎榜type=01有{tag_str}资金的票"
        if tag_str == '无标签':
            st['desc'] = "龙虎榜type=01无特殊资金标签的票"
        strategies.append(st)

# 5b. 赚钱/亏钱席位
if win_seat_trades:
    st = calc_stats(win_seat_trades, '😎赚钱席位买入')
    if st: strategies.append(st)
if lose_seat_trades:
    st = calc_stats(lose_seat_trades, '😰亏钱席位买入')
    if st: strategies.append(st)

# 5c. 全量基准
all_trades = []
for _, trades in tagged_trades.items():
    all_trades.extend(trades)
st = calc_stats(all_trades, '📊type=01全量(严格时间线)')
if st: strategies.append(st)

# 排序：按综合分
def composite(s):
    ret = s.get('close_ret', 0)
    wr = s.get('close_win', 0)
    n = s.get('n', 0)
    return ret * 4 + wr * 0.3 + min(n / 50, 1) * 15

strategies.sort(key=lambda x: -composite(x))

print(f"\n生成策略: {len(strategies)}个")
for s in strategies[:10]:
    print(f"  {s['name'][:40]:40s} ret={s['close_ret']:+.2f}% wr={s['close_win']:.1f}% n={s['n']}")

# ===== 6. 加载已有v2.json，替换strategies =====
if os.path.exists(V2_PATH):
    with open(V2_PATH) as f:
        v2 = json.load(f)
else:
    v2 = {
        'version': '严格时间线回测 v1.0 (替换旧版)',
        'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_samples': len(all_trades),
        'trade_rule': 'T-1龙虎榜(type=01)→T开盘买→T+1收盘卖',
        'sample': '纯主板(6/000/001/002/003,排除300创业板)',
        'data_quality': {
            'grade': 'A级(样本≥100)',
            'kline_coverage_pct': 73.3,
            'bias_note': '主板only, 震荡期占67%样本'
        },
        'overall': {
            'close_mean': 0.04,
            'close_win': 45.4,
            'open_mean': 0.1,
            'open_win': 44.1,
        },
    }

# 更新overall
if all_trades:
    v2['total_samples'] = len(all_trades)
    v2['overall']['close_mean'] = round(sum(t['close_ret'] for t in all_trades) / len(all_trades), 2)
    v2['overall']['close_win'] = round(sum(1 for t in all_trades if t['is_win']) / len(all_trades) * 100, 1)
    v2['overall']['open_mean'] = round(sum(t['open_ret'] for t in all_trades) / len(all_trades), 2)

v2['strategies'] = strategies
v2['generated_by'] = 'generate_v2_strategies.py'
v2['generated_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')

with open(V2_PATH, 'w') as f:
    json.dump(v2, f, ensure_ascii=False, indent=2)

print(f"\n✅ 已保存到 {V2_PATH}")
print(f"   共 {len(strategies)} 个策略")

conn.close()
conn_k.close()
