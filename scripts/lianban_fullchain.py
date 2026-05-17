#!/usr/bin/env python3
"""
连板全链路研究 — 从1板到5板的完整晋级路径
核心：
1. 各层的晋级率（1→2, 2→3, 3→4, 4→5）
2. 各层T+1收益对比
3. 哪些股票/板块能走到4板5板
4. 每层晋级的关键特征
"""
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

DB = "/home/ubuntu/astock/data/kline_cache.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

SECTORS = {
    'chip': ['603986','603019','600584','603005','603160','002049','600171','603893','002185','300672','300661','688525','688110'],
    'gpu': ['601138','603019','000977','600498','000063','002916','300308','688041'],
    'semicon': ['688981','688012','688008','688126','688396','002371','688072','688120','688037','300661','688019','688200'],
    'robot': ['002472','002896','300124','688160','300660','688017','300580','601689','603662'],
    'ai_app': ['300624','002230','300418','603533','002555','300058','300315','002517','688111'],
    'low_alt': ['002085','600580','300177','688070','688568','002111','002023','603885','000099','600391'],
    'battery': ['300750','002074','300014','002460','002709','600884','300073','300568','002812','300769'],
    'oil': ['600028','601857','600688','600339','600871','000059','000819','000096','000554','601808','600583','002278','002207','002554','603619','600295'],
    'gold': ['600489','600547','601899','002155','000975','600988','601069','002237','600531','600766','000506','002716','600311','600385'],
    'metal': ['600362','000630','000603','000751','002114','000960','600497','601600','000807','600219','002460','002466','600516','600010','600019','000629'],
}
code2sector = {}
for sec, codes in SECTORS.items():
    for code in codes:
        code2sector[code] = sec

all_codes = list(set(c for codes in SECTORS.values() for c in codes))

def is_ban(chg):
    return chg >= 9.5

all_kline = defaultdict(list)
for code in all_codes:
    c.execute("SELECT date, open, close, high, low, volume FROM kline WHERE code=? AND date>='2024-01-01' AND date<='2026-04-30' ORDER BY date", (code,))
    rows = c.fetchall()
    all_kline[code] = rows

# ═══════════════════════════════════════════
# 第一步：建立全链路分层池
# ═══════════════════════════════════════════

# layers[N] = N板池 {(code, date): {特征}}
# 1板池 → 2板池 → 3板池 → 4板池 → 5板池
layers = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}}  # 注意改为大括号

for code in all_codes:
    rows = all_kline[code]
    if len(rows) < 60: continue
    
    for i in range(len(rows)):
        r = rows[i]
        chg = (r[2] - r[1]) / r[1] * 100 if r[1] > 0 else 0
        if not is_ban(chg): continue
        
        # 回溯找连板数
        ban_count = 1
        j = i - 1
        while j >= 0:
            rj = rows[j]
            rj_chg = (rj[2] - rj[1]) / rj[1] * 100 if rj[1] > 0 else 0
            if is_ban(rj_chg):
                ban_count += 1
                j -= 1
            else:
                break
        
        key = (code, r[0])
        open_p, close, high, low, volume = r[1], r[2], r[3], r[4], r[5]
        amp = (high - low) / open_p * 100 if open_p > 0 else 0
        open_chg = (open_p - rows[i-1][2]) / rows[i-1][2] * 100 if i > 0 and rows[i-1][2] > 0 else 0
        
        ma20_vol = sum(rows[k][5] for k in range(max(0,i-20), i)) / max(1, i - max(0,i-20))
        vol_ratio = volume / ma20_vol if ma20_vol > 0 else 1
        prev_chg = (rows[i-1][2] - rows[i-1][1]) / rows[i-1][1] * 100 if i > 0 and rows[i-1][1] > 0 else 0
        
        # 相对前一天的量比
        prev_vol = rows[i-1][5] if i > 0 else 1
        vol_vs_prev = volume / prev_vol if prev_vol > 0 else 1
        
        t1_ret = None
        is_next_ban = False
        if i+1 < len(rows):
            t1_ret = (rows[i+1][2] - close) / close * 100
            nchg = (rows[i+1][2] - rows[i+1][1]) / rows[i+1][1] * 100 if rows[i+1][1] > 0 else 0
            is_next_ban = is_ban(nchg)
        
        feat = {
            'date': r[0], 'code': code,
            'amp': round(amp, 2), 'vol_ratio': round(vol_ratio, 2),
            'open_chg': round(open_chg, 2), 'prev_chg': round(prev_chg, 2),
            'vol_vs_prev': round(vol_vs_prev, 2),
            'close': close,
            't1_ret': round(t1_ret, 2) if t1_ret is not None else None,
            'is_next_ban': is_next_ban,
            'ban_count': ban_count,
            'sec': code2sector.get(code, ''),
        }
        
        layers[ban_count][key] = feat

print("=" * 100)
print("【分层全链路统计】")
print("=" * 100)

print(f"{'层级':<8s} {'样本数':>8s} {'T+1均':>10s} {'T+1胜率':>10s} {'晋级数':>8s} {'晋级率':>8s}")
print("-" * 55)

# 计算晋级链：每个层有多少次晋级到下一层
# 第N板到第N+1板的晋级 = N板池中 is_next_ban=True 的数量
prev_pool_count = 0
for level in range(1, 6):
    pool = layers[level]
    pool_count = len(pool)
    
    if level > 1:
        promote_rate = pool_count / prev_pool_count * 100 if prev_pool_count > 0 else 0
    else:
        promote_rate = 100.0
    
    t1_rets = [v['t1_ret'] for v in pool.values() if v['t1_ret'] is not None]
    next_bans = sum(1 for v in pool.values() if v['is_next_ban'])
    
    if t1_rets:
        avg_t1 = sum(t1_rets) / len(t1_rets)
        wr = sum(1 for v in t1_rets if v > 0) / len(t1_rets) * 100
    else:
        avg_t1 = 0
        wr = 0
    
    print(f"{level}板池({level-1}→{level}):{pool_count:>7d} {avg_t1:>+9.2f}% {wr:>9.1f}% {next_bans:>8d} {promote_rate:>7.1f}%")
    prev_pool_count = pool_count

print()

# ═══════════════════════════════════════════
# 维度A: 各层的板块分布
# ═══════════════════════════════════════════
print("=" * 100)
print("【维度A】各层板块分布 — 谁在每层最多？")
print("=" * 100)

for level in range(1, 6):
    pool = layers[level]
    if not pool: continue
    
    sec_count = defaultdict(int)
    for v in pool.values():
        sec_count[v['sec']] += 1
    
    total = len(pool)
    top3 = sorted(sec_count.items(), key=lambda x: -x[1])[:3]
    
    print(f"\n{level}板池(共{total}次):")
    for sec, cnt in top3:
        print(f"  {sec:<10s} {cnt:>4d}次({cnt/total*100:.0f}%)")

print()

# ═══════════════════════════════════════════
# 维度B: 全链路明细 — 每个连板标的的完整路径
# ═══════════════════════════════════════════
print("=" * 100)
print("【维度B】全链路路径明细 — 从1板到5板的完整路径")
print("=" * 100)

# 对每个连板>=2的标的，建立完整路径
# 从1板开始->2板->3板->... 直到断板
chain_paths = []

for code in all_codes:
    rows = all_kline[code]
    if len(rows) < 60: continue
    
    # 收集该股所有涨停日
    ban_dates = []
    for i, r in enumerate(rows):
        chg = (r[2] - r[1]) / r[1] * 100 if r[1] > 0 else 0
        if is_ban(chg):
            ban_dates.append({'date': r[0], 'close': r[2], 'idx': i})
    
    # 建立连板链
    i = 0
    while i < len(ban_dates):
        start = i
        end = i
        while end + 1 < len(ban_dates):
            d1 = datetime.strptime(ban_dates[end]['date'], '%Y-%m-%d')
            d2 = datetime.strptime(ban_dates[end+1]['date'], '%Y-%m-%d')
            if (d2 - d1).days <= 3:  # 连续交易日
                end += 1
            else:
                break
        
        chain_len = end - start + 1
        if chain_len >= 2:
            path = []
            for j in range(start, end+1):
                d = ban_dates[j]['date']
                idx = ban_dates[j]['idx']
                
                r = rows[idx]
                open_p, close, high, low, volume = r[1], r[2], r[3], r[4], r[5]
                amp = (high - low) / open_p * 100 if open_p > 0 else 0
                open_chg = (open_p - rows[idx-1][2]) / rows[idx-1][2] * 100 if idx > 0 and rows[idx-1][2] > 0 else 0
                ma20_vol = sum(rows[k][5] for k in range(max(0,idx-20), idx)) / max(1, idx - max(0,idx-20))
                vol_ratio = volume / ma20_vol if ma20_vol > 0 else 1
                
                path.append({
                    'date': d, 'order': j - start + 1,
                    'amp': round(amp, 2), 'open_chg': round(open_chg, 2),
                    'vol_ratio': round(vol_ratio, 2),
                })
            
            chain_paths.append({
                'code': code, 'sec': code2sector.get(code, ''),
                'chain_len': chain_len,
                'path': path,
            })
        
        i = end + 1

# 按连板长度排序展示
for cp in sorted(chain_paths, key=lambda x: -x['chain_len']):
    print(f"\n{cp['code']}({cp['sec']}) {cp['chain_len']}连板:")
    for p in cp['path']:
        print(f"  第{p['order']}板 {p['date']} 开{p['open_chg']:+.1f}% 振幅{p['amp']:.1f}% 量比{p['vol_ratio']:.1f}")

print()

# ═══════════════════════════════════════════
# 维度C: 各层的特征对比 — 什么样的N板能晋级N+1板
# ═══════════════════════════════════════════
print("=" * 100)
print("【维度C】各层晋级特征 — 能晋级的vs不能的，特征差异")
print("=" * 100)

for level in range(1, 5):
    pool = layers[level]
    if len(pool) < 3: continue
    
    promoted = [(k, v) for k, v in pool.items() if v['is_next_ban']]
    not_promoted = [(k, v) for k, v in pool.items() if not v['is_next_ban']]
    
    if not promoted: continue
    
    print(f"\n{level}板→{level+1}板:")
    print(f"  能晋级的{len(promoted)}次, 没晋级的{len(not_promoted)}次")
    
    fields = [('开盘(open_chg)', 'open_chg'), ('振幅(amp)', 'amp'), 
              ('量比(vol_ratio)', 'vol_ratio'), ('前日涨(prev_chg)', 'prev_chg'),
              ('相对昨量(vol_vs_prev)', 'vol_vs_prev')]
    
    for fname, field in fields:
        p_vals = [v[field] for _, v in promoted if v.get(field) is not None]
        n_vals = [v[field] for _, v in not_promoted if v.get(field) is not None]
        if p_vals and n_vals:
            p_avg = sum(p_vals)/len(p_vals)
            n_avg = sum(n_vals)/len(n_vals)
            diff = p_avg - n_avg
            star = ' ✅' if abs(diff) > 1 else ''
            print(f"  {fname:<12s}: 晋级均值{p_avg:>+8.2f} vs 非晋级均值{n_avg:>+8.2f} 差{diff:>+8.2f}{star}")

print()

# ═══════════════════════════════════════════
# 维度D: T+1收益分层
# ═══════════════════════════════════════════
print("=" * 100)
print("【维度D】T+1收益分层 — 每层买入后的T+1期望")
print("=" * 100)

print(f"{'层级':<8s} {'样本':>6s} {'T+1均':>8s} {'T+1胜率':>8s} {'最佳':>8s} {'最差':>8s}")
print("-" * 55)

for level in range(1, 6):
    pool = layers[level]
    t1_rets = [v['t1_ret'] for v in pool.values() if v['t1_ret'] is not None]
    if not t1_rets: continue
    avg = sum(t1_rets)/len(t1_rets)
    wr = sum(1 for v in t1_rets if v > 0)/len(t1_rets)*100
    print(f"{level}板池:{len(t1_rets):>6d} {avg:>+7.2f}% {wr:>7.1f}% {max(t1_rets):>+7.2f}% {min(t1_rets):>+7.2f}%")

print()

# ═══════════════════════════════════════════
# 维度E: 每层最佳操作策略
# ═══════════════════════════════════════════
print("=" * 100)
print("【维度E】分层操作策略 — 每层做什么")
print("=" * 100)

for level in range(1, 6):
    pool = layers[level]
    if not pool: continue
    
    p_vals = [v for v in pool.values() if v['is_next_ban']]
    np_vals = [v for v in pool.values() if not v['is_next_ban']]
    
    promote_rate = len(p_vals) / len(pool) * 100
    
    t1_rets = [v['t1_ret'] for v in pool.values() if v['t1_ret'] is not None]
    avg_t1 = sum(t1_rets)/len(t1_rets) if t1_rets else 0
    wr_t1 = sum(1 for v in t1_rets if v > 0)/len(t1_rets)*100 if t1_rets else 0
    
    print(f"\n{level}板({len(pool)}次):")
    print(f"  →{level+1}板晋级率: {promote_rate:.1f}%")
    print(f"  T+1均: {avg_t1:+.2f}% 胜率: {wr_t1:.1f}%")
    
    # 给出操作建议
    if promote_rate > 20:
        print(f"  操作: ✅ 可以赌{level+1}板(晋级率{promote_rate:.0f}%)")
    elif promote_rate > 5:
        print(f"  操作: ⚠️ 谨慎赌{level+1}板(晋级率{promote_rate:.0f}%)")
    else:
        print(f"  操作: ❌ 不要赌{level+1}板(晋级率{promote_rate:.0f}%)")
    
    if level == 1:
        # 1板中能晋级2板的特征
        if p_vals and np_vals:
            fields = ['prev_chg', 'vol_ratio', 'amp', 'open_chg']
            print(f"  能晋级的1板特征:")
            for f in fields:
                p_avg = sum(v[f] for v in p_vals if v.get(f) is not None)/len(p_vals)
                n_avg = sum(v[f] for v in np_vals if v.get(f) is not None)/len(np_vals)
                print(f"    {f}: {p_avg:+.2f} vs {n_avg:+.2f}")

print(f"\n{'='*100}")
print("连板全链路研究完成！")
print("=" * 100)

conn.close()
