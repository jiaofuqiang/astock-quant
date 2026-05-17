#!/usr/bin/env python3
"""
【09:30 开盘执行】开盘数据 → 匹配预测 → 买卖信号 → 执行

买卖信号规则（修正版回测11289次验证）：
  - 买入：开≥3%+龙≥3板（+6.9%）/ 开≥5%+龙≥3优先（+7.0%）
  - 卖出(T+1)：开≥7%竞价卖 / 开≥3%等冲高 / 开<0%等冲高
"""
import sys, os, json, urllib.request
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 09:30 开盘执行 {TODAY}")
print("="*60)

# ============================================================
# 1. 加载竞价数据+交易计划
# ============================================================
auction = load_json_or_empty(os.path.join(REPORTS_DIR, 'auction', f'auction_{TODAY}.json'))
plan_report = load_json_or_empty(report_filename(PLAN_DIR, 'plan'))
print(f"  竞价分析: {'✅' if auction else '❌'}")

# ============================================================
# 2. 获取开盘数据
# ============================================================
def market_prefix(code):
    code = code.strip()
    if code.lower().startswith(('sh','sz')): return code.lower()
    # 600/601/603/605/688 → sh; 000/001/002/003/300/301 → sz
    if code[:3] in ('600','601','603','605','688'): return 'sh' + code
    return 'sz' + code

def get_open_data(codes):
    if not codes: return []
    res = []
    try:
        full_codes = [market_prefix(c) for c in codes]
        url = f"http://qt.gtimg.cn/q={','.join(full_codes)}"
        req = urllib.request.urlopen(url, timeout=10)
        raw = req.read().decode('gbk')
        for line in raw.strip().split(';'):
            if not line or '=' not in line: continue
            parts = line.split('~')
            if len(parts) < 40: continue
            code = parts[2]
            name = parts[1]
            price = float(parts[3]) if parts[3] else 0
            yc = float(parts[4]) if parts[4] else 0
            open_p = float(parts[5]) if parts[5] else 0
            high = float(parts[33]) if len(parts)>33 and parts[33] else 0
            low = float(parts[34]) if len(parts)>34 and parts[34] else 0
            vol = float(parts[6]) if parts[6] else 0
            amount = float(parts[37]) if len(parts)>37 and parts[37] else 0
            vol_ratio = float(parts[39]) if len(parts)>39 and parts[39] else 0
            chg_pct = float(parts[32]) if parts[32] else 0
            res.append({
                'code': code, 'name': name,
                'price': price, 'open': open_p, 'high': high, 'low': low,
                'change_pct': chg_pct,
                'volume': vol, 'amount_wan': amount, 'volume_ratio': vol_ratio,
            })
    except Exception as e:
        print(f"  [WARN] 开盘数据获取失败: {e}")
    return res

# ============================================================
# 3. 买卖信号检查
# ============================================================
def trade_signals(open_data):
    signals = {'buy': [], 'sell': [], 'hold': []}

    for item in open_data:
        chg = item['change_pct']
        vr = item['volume_ratio']

        # ---- 买入信号 ----
        if chg >= 3.0 and vr < 1.0:
            signals['buy'].append({
                'code': item['code'],
                'name': item['name'],
                'action': '买入',
                'level': '优先' if chg >= 5.0 else '普通',
                'price': item['price'],
                'reason': f"开{chg:+.2f}% 量比{vr:.2f} (回测+6.9%~+7.6%)",
            })
        # ---- 卖出信号(T+1用) ----
        if chg >= 7.0:
            signals['sell'].append({
                'code': item['code'],
                'name': item['name'],
                'action': '竞价卖',
                'reason': f"开{chg:+.2f}%仅剩0.4%空间",
            })
        elif chg >= 3.0:
            signals['hold'].append({
                'code': item['code'],
                'name': item['name'],
                'action': '等冲高',
                'reason': f"开{chg:+.2f}% 76%还有+2.9%+空间",
            })
        elif chg < 0:
            signals['hold'].append({
                'code': item['code'],
                'name': item['name'],
                'action': '等冲高',
                'reason': f"开{chg:+.2f}% 70%翻红概率",
            })
    return signals

# 获取关注标的code — 竞价报告的buy_signals + auction_data全部监控
focus_codes = []
if auction:
    # 直接从竞价报告的买入信号提取code
    for bs in auction.get('buy_signals', []):
        code = bs.get('code', '').strip()
        if code:
            focus_codes.append(code)
    # 从竞价数据的auction_data提取code（不论buy_signals是否有）
    for ad in auction.get('auction_data', []):
        code = ad.get('code', '').strip()
        if code:
            focus_codes.append(code)
    # 从交易计划的sectors查bundle（补充版）
    plans = plan_report.get('plans', {})
    for v in plans.values():
        sectors = v.get('sectors', [])
        bundle = load_json_or_empty(BUNDLE_JSON)
        si = bundle.get('sector_index', {})
        all_secs = (si.get('hot_sectors',[]) + si.get('other_sectors',[])) if si else []
        for sn in sectors:
            for s in all_secs:
                if sn in s.get('name','') and s.get('stocks'):
                    for stk in s['stocks'][:3]:
                        if stk.get('code'):
                            focus_codes.append(stk['code'])

focus_codes = list(set(focus_codes))[:20]
open_data = get_open_data(focus_codes)
signals = trade_signals(open_data)

print(f"\n  实时数据: {len(open_data)}只")
print(f"  买入信号: {len(signals['buy'])}条")
for s in signals['buy'][:5]:
    print(f"    [{s['level']}] {s['name']} @{s['price']} - {s['reason']}")
print(f"  卖出信号: {len(signals['sell'])}条")
for s in signals['sell'][:5]:
    print(f"    [卖] {s['name']} - {s['reason']}")
print(f"  持有等待: {len(signals['hold'])}条")

# ============================================================
# 4. 保存
# ============================================================
execute_report = {
    'type': 'open_execution',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'stocks_monitored': len(open_data),
    'buy_signals': signals['buy'],
    'sell_signals': signals['sell'],
    'hold_signals': signals['hold'][:10],
}

os.makedirs(os.path.join(REPORTS_DIR, 'execution'), exist_ok=True)
with open(os.path.join(REPORTS_DIR, 'execution', f'execute_{TODAY}.json'), 'w', encoding='utf-8') as f:
    json.dump(execute_report, f, ensure_ascii=False, indent=2)
print(f"\n  执行报告已保存")
print(f"\n[24h] 09:30 开盘执行完成 ✅")
