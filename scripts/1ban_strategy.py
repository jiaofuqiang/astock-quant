#!/usr/bin/env python3
"""
1板（首板涨停）策略深度研究
基于kline_cache回测：什么1板能赚钱，什么1板是坑
"""
import sqlite3, json
from collections import defaultdict
from datetime import datetime, timedelta

DB = "/home/ubuntu/astock/data/kline_cache.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

# 核心标的
core_codes = {
    '603986':'兆易创新','603019':'中科曙光','600584':'长电科技','603005':'晶方科技',
    '603160':'汇顶科技','601138':'工业富联','002049':'紫光国微','600171':'上海贝岭',
    '603893':'瑞芯微','300672':'国科微','300661':'圣邦股份','002185':'华天科技',
    '688525':'佰维存储','688110':'东芯股份','688008':'澜起科技','688981':'中芯国际',
    '688012':'中微公司','002371':'北方华创','300750':'宁德时代','002074':'国轩高科',
    '300014':'亿纬锂能','002460':'赣锋锂业','002709':'天赐材料','002812':'恩捷股份',
    '300624':'万兴科技','300418':'昆仑万维','603533':'掌阅科技','002555':'三七互娱',
    '002085':'万丰奥威','002472':'双环传动','002896':'中大力德','002916':'深南电路',
    '000977':'浪潮信息',
}

# 1板定义：当天涨停(chg>=9.5%)，且前一天没涨停(非连板)
print("=" * 75)
print("一板涨停策略研究 — 基于K线历史数据")
print("=" * 75)

all_1ban = []

for code, name in core_codes.items():
    c.execute("""SELECT date, open, close, high, volume FROM kline 
                 WHERE code=? AND date>='2024-01-01' ORDER BY date""", (code,))
    rows = c.fetchall()
    if len(rows) < 30: continue
    
    for i in range(1, len(rows)):
        date, open_p, close, high, volume = rows[i]
        chg = (close - open_p) / open_p * 100 if open_p > 0 else 0
        prev_close = rows[i-1][2]
        prev_chg = (prev_close - rows[i-1][1]) / rows[i-1][1] * 100 if rows[i-1][1] > 0 else 0
        
        # 1板条件：今天涨停，前一天没涨停
        is_zt = chg >= 9.5
        prev_not_zt = prev_chg < 9.0
        
        if not (is_zt and prev_not_zt):
            continue
        
        # 获取后续N天数据
        t_plus = {}
        for offset in range(1, 11):
            idx = i + offset
            if idx >= len(rows): break
            t_plus[f'T+{offset}'] = {
                'close': rows[idx][2],
                'high': rows[idx][3],
                'open': rows[idx][1],
                'date': rows[idx][0],
            }
        
        if 'T+1' not in t_plus:
            continue
        
        base = close  # 1板收盘价（你的买入价）
        t1 = t_plus['T+1']
        t1_open_ret = (t1['open'] - base) / base * 100
        t1_close_ret = (t1['close'] - base) / base * 100
        t1_high_ret = (t1['high'] - base) / base * 100
        
        t2_ret = None
        t5_ret = None
        if 'T+2' in t_plus:
            t2_ret = (t_plus['T+2']['close'] - base) / base * 100
        if 'T+5' in t_plus:
            t5_ret = (t_plus['T+5']['close'] - base) / base * 100
        
        # 是否连板（T+1再度涨停）
        is_lianban = t1_close_ret >= 9.5
        
        # 20日均量
        vol_list = [r[4] for r in rows[max(0,i-20):i]]
        ma_vol = sum(vol_list) / len(vol_list) if vol_list else 1
        vol_ratio = volume / ma_vol if ma_vol > 0 else 0
        
        all_1ban.append({
            'code': code, 'name': name, 'date': date,
            'T+1_open': round(t1_open_ret, 2),
            'T+1_close': round(t1_close_ret, 2),
            'T+1_high': round(t1_high_ret, 2),
            'T+2': round(t2_ret, 2) if t2_ret else None,
            'T+5': round(t5_ret, 2) if t5_ret else None,
            'is_lianban': is_lianban,
            'vol_ratio': round(vol_ratio, 2),
            'prev_chg': round(prev_chg, 2),
            'chg': round(chg, 2),
        })

print(f"\n总1板样本: {len(all_1ban)}次")
print()

# ── 1板次日的收益分布 ──
print("【1】1板次日收益全景")
print("-" * 50)
t1_closes = [r['T+1_close'] for r in all_1ban]
t1_highs = [r['T+1_high'] for r in all_1ban]
t1_opens = [r['T+1_open'] for r in all_1ban]

for name, vals in [('T+1开盘', t1_opens), ('T+1收盘', t1_closes), ('T+1最高', t1_highs)]:
    w = sum(1 for v in vals if v > 0)
    avg = sum(vals) / len(vals)
    top = sorted(vals, reverse=True)[:3]
    bot = sorted(vals)[:3]
    print(f"  {name}: 均{avg:+.2f}% 胜率{w/len(vals)*100:.1f}% | TOP:{top[0]:+.1f}%/{top[1]:+.1f}%/{top[2]:+.1f}% | BOT:{bot[0]:+.1f}%/{bot[1]:+.1f}%/{bot[2]:+.1f}%")
    print(f"    中位数: {sorted(vals)[len(vals)//2]:+.2f}%")

# ── 连板率 ──
lianban = [r for r in all_1ban if r['is_lianban']]
print(f"\n连板率: {len(lianban)}/{len(all_1ban)} = {len(lianban)/len(all_1ban)*100:.1f}%")
print(f"连板次日平均收益: {sum(r['T+1_close'] for r in lianban)/len(lianban):+.2f}%")
non_lianban = [r for r in all_1ban if not r['is_lianban']]
print(f"非连板次日平均收益: {sum(r['T+1_close'] for r in non_lianban)/len(non_lianban):+.2f}%")

# ── 什么1板容易连板 ──
print(f"\n【2】什么1板容易连板？")
print("-" * 50)

# 按量比分组
print("按量比分:")
for cut in [(0,1,'缩量板<1'), (1,2,'正常1-2'), (2,5,'放量2-5'), (5,100,'天量>5')]:
    sub = [r for r in all_1ban if cut[0] <= r['vol_ratio'] < cut[1]]
    if not sub: continue
    lb = sum(1 for r in sub if r['is_lianban'])
    avg_t1 = sum(r['T+1_close'] for r in sub) / len(sub)
    print(f"  {cut[2]}: {len(sub):>2d}次 连板{lb/len(sub)*100:5.1f}% 均T+1{avg_t1:+6.2f}%")

# 按前日涨幅分组
print("按前日涨幅分:")
for cut in [(-20, -3, '前日大跌'), (-3, 3, '前日平'), (3, 7, '前日小涨'), (7, 20, '前日大涨')]:
    sub = [r for r in all_1ban if cut[0] <= r['prev_chg'] < cut[1]]
    if not sub: continue
    lb = sum(1 for r in sub if r['is_lianban'])
    avg_t1 = sum(r['T+1_close'] for r in sub) / len(sub)
    print(f"  {cut[2]}: {len(sub):>2d}次 连板{lb/len(sub)*100:5.1f}% 均T+1{avg_t1:+6.2f}%")

# ── 买点分析：T+1竞价买 vs 开盘追 vs 排板 ──
print(f"\n【3】1板买入策略对比")
print("-" * 50)
print(f"（模拟：你是T日打板成功，第T+1日做决策）")

# T+1高开率（开盘>0%）
gaokai = sum(1 for r in all_1ban if r['T+1_open'] > 0) / len(all_1ban) * 100
print(f"  T+1高开概率: {gaokai:.1f}%")

# T+1低开率
dikai = sum(1 for r in all_1ban if r['T+1_open'] < 0) / len(all_1ban) * 100
print(f"  T+1低开概率: {dikai:.1f}%")

# 开盘买入收益 vs 竞价卖出收益
avg_open_buy = sum(r['T+1_close'] for r in all_1ban) / len(all_1ban)  # 开盘买收盘卖
avg_hold = sum(r['T+1_close'] for r in all_1ban) / len(all_1ban)  # 等一天
avg_open_sell = sum(r['T+1_open'] for r in all_1ban) / len(all_1ban)  # 开盘就卖
print(f"  策略A(前日打板成功→次日开盘卖): 均{avg_open_sell:+.2f}%")
print(f"  策略B(前日打板成功→次日收盘卖): 均{avg_close_ret:.2f}%" if False else "")

# T+1开盘买 vs 等低吸
better_than_open = sum(1 for r in all_1ban if r['T+1_close'] > r['T+1_open']) / len(all_1ban) * 100
print(f"  开盘后还能继续涨(收盘>开盘): {better_than_open:.1f}%")
buy_low_ret = sum(1 for r in all_1ban if r['T+1_high'] > max(0, r['T+1_open']) and r['T+1_open'] > -3) 
# T+1日内先跌后涨的比例
xian_die = sum(1 for r in all_1ban if r['T+1_open'] < r['T+1_close'] and r['T+1_open'] < 0)
print(f"  低开高走: {xian_die}次 ({xian_die/len(all_1ban)*100:.1f}%)")

# ── 不看T日分析，直接看T+1怎么赚钱 ──
print(f"\n【4】T+1日操作策略（1板后的次日）")
print("-" * 50)

# 如果T+1竞价高开>3%（强势延续）
qiangshi = [r for r in all_1ban if r['T+1_open'] > 3]
ruoshi = [r for r in all_1ban if r['T+1_open'] < -2]
putong = [r for r in all_1ban if -2 <= r['T+1_open'] <= 3]

for name, sub in [('竞价高开>3%(强势)', qiangshi), ('竞价-2%~3%(普通)', putong), ('竞价低开<-2%(弱势)', ruoshi)]:
    if not sub: continue
    avg_c = sum(r['T+1_close'] for r in sub) / len(sub)
    avg_h = sum(r['T+1_high'] for r in sub) / len(sub)
    avg_o = sum(r['T+1_open'] for r in sub) / len(sub)
    w_c = sum(1 for r in sub if r['T+1_close'] > 0) / len(sub) * 100
    print(f"  {name}")
    print(f"    T+1开盘{avg_o:+.2f}% → 收盘{avg_c:+.2f}% (胜率{w_c:.1f}%) 盘中最高{avg_h:+.2f}%")

# ── 不同1板胜率对比 ──
print(f"\n【5】1板 vs 非涨停的T+1收益对比")
print("-" * 50)
zt_signals = [r for r in all_1ban if r['chg'] >= 9.5]
print(f"  1板涨停: {len(zt_signals)}次 均T+1{sum(r['T+1_close'] for r in zt_signals)/len(zt_signals):+.2f}%")

# 查非涨停但合力≥75的信号（之前回测数据）
with open("/home/ubuntu/astock/data/backtest_results.json") as f:
    bt = json.load(f)
bt_zt = [r for r in bt['results'] if r['chg'] >= 9.5]
bt_nozt = [r for r in bt['results'] if r['chg'] < 9.5]
print(f"  回测非涨停: {len(bt_nozt)}次 均T+1{sum(r['t1_ret'] for r in bt_nozt)/len(bt_nozt):+.2f}%")

# ── 不同时间窗口的T+1表现 ──
print(f"\n【6】1板的时间窗口效应")
print("-" * 50)
c.execute("SELECT DISTINCT date FROM kline WHERE date>='2024-01-01' ORDER BY date")
all_dates = [r[0] for r in c.fetchall()]
from datetime import datetime
weekday_map = {}
# 简化
for r in all_1ban:
    try:
        wd = datetime.strptime(r['date'], '%Y-%m-%d').weekday()
    except:
        continue
    if wd not in weekday_map:
        weekday_map[wd] = []
    weekday_map[wd].append(r['T+1_close'])

wd_names = {0:'周一',1:'周二',2:'周三',3:'周四',4:'周五'}
for wd in sorted(weekday_map.keys()):
    vals = weekday_map[wd]
    avg = sum(vals)/len(vals)
    w = sum(1 for v in vals if v > 0)/len(vals)*100
    print(f"  {wd_names[wd]}: {len(vals)}次 均{avg:+.2f}% 胜率{w:.1f}%")

conn.close()

print(f"\n{'='*75}")
print(f"结论摘要")
print(f"{'='*75}")
print(f"""
1. 1板次日的期望收益: {sum(r['T+1_close'] for r in all_1ban)/len(all_1ban):+.2f}%
2. 连板概率: {len(lianban)/len(all_1ban)*100:.1f}%
3. 最佳买点: {"竞价高开>3%可竞价追" if qiangshi and sum(r['T+1_close'] for r in qiangshi)/len(qiangshi)>0 else "开盘后看"}
4. 核心风险: 低开-2%以下的板基本没救（需结合）
""")
