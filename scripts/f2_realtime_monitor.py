#!/usr/bin/env python3
"""
🚨 F2盘尾选股实时监控 v1.0
每10分钟扫描F2/F2+/盘尾信号，发现新信号立刻推送微信。

与three_funds_f2.py共享核心逻辑，但额外：
1. 维护历史状态 → 发现新信号才推送（不重复推送）
2. 信号等级分类 → 推送到微信时带醒目等级标识
3. 盘中任何时段运行 → 不需要等到14:55

用法：
  python3 scripts/f2_realtime_monitor.py --now     # 单次检查（推荐cron使用）
  python3 scripts/f2_realtime_monitor.py --status  # 查看当前状态

cron配置：
  */10 9-15 * * 1-5
"""
import os, sys, json, re, subprocess
from datetime import datetime, date
import sqlite3
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
DB_PATH = os.path.join(BASE, "data", "kline_cache.db")
MAINBOARD_FILE = os.path.join(BASE, "data", "all_main_board.txt")
STATE_FILE = os.path.join(BASE, "data", "f2_monitor_state.json")

# ============================================================
# 板块成分股（与three_funds_f2.py同步）
# ============================================================
SECTORS = {
    'chip': {'name': '存储芯片/AI芯片', 'codes': ['603986','603019','600584','603005','603160',
              '002049','600171','603893','002185','300655','300672','300661','688525','688110']},
    'gpu': {'name': 'AI算力/服务器', 'codes': ['601138','603019','000977','600498','000063',
            '002916','300308','688041']},
    'semicon': {'name': '半导体设备/材料', 'codes': ['688981','688012','688008','688126','688396',
               '002371','688072','688120','688037','300661','688019','688200']},
    'robot': {'name': '人形机器人', 'codes': ['002472','002896','300124','688160','300660',
             '688017','300580','601689','603662']},
    'ai_app': {'name': 'AI应用/AIGC', 'codes': ['300624','002230','300418','603533','002555',
              '300058','300315','300624','002517','688111']},
    'low_alt': {'name': '低空经济/飞行汽车', 'codes': ['002085','600580','300177','688070','688568',
               '002111','002023','603885','000099','600391']},
    'battery': {'name': '固态电池/新能源', 'codes': ['300750','002074','300014','002460','002709',
               '600884','300073','300568','002812','300769']},
}

ALL_CODES = list(dict.fromkeys(c for s in SECTORS.values() for c in s['codes']))
CODE_TO_SECTOR = {}
for sec_key, sec_info in SECTORS.items():
    for code in sec_info['codes']:
        CODE_TO_SECTOR[code] = {'key': sec_key, 'name': sec_info['name']}

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)

def sf(v):
    try: return float(v) if v and v != '-' else 0.0
    except: return 0.0

# ============================================================
# 腾讯实时行情
# ============================================================
def tencent_quote_batch(codes, batch_size=30):
    if not codes:
        return {}
    result = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        def mkt(code):
            code = code.strip()
            return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, timeout=12
            )
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line:
                    continue
                parts = line.split('=', 1)
                if len(parts) < 2:
                    continue
                eq_key, val = parts
                raw_code = eq_key.strip().split('_')[-1]
                code = raw_code[2:] if raw_code.startswith(('sh','sz')) else raw_code
                if not val or val == '""':
                    continue
                fields = val.strip('"').split('~')
                if len(fields) < 44:
                    continue
                name = fields[1]
                price = sf(fields[3])
                prev_close = sf(fields[4])
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                open_p = sf(fields[5])
                high = sf(fields[33])
                low = sf(fields[34])
                volume = sf(fields[6])
                amount = sf(fields[37])
                turnover = sf(fields[38])
                amplitude = sf(fields[43])

                result[code] = {
                    'name': name, 'price': price, 'prev_close': prev_close,
                    'change_pct': round(change_pct, 2),
                    'open': open_p, 'high': high, 'low': low,
                    'volume': volume, 'amount': amount,
                    'turnover': turnover, 'amplitude': amplitude,
                }
        except Exception:
            pass
    return result

# ============================================================
# K线数据
# ============================================================
def get_kline(code, limit=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("""
            SELECT date, open, close, high, low, volume
            FROM kline WHERE code=? ORDER BY date DESC LIMIT ?
        """, (code, limit))
        rows = c.fetchall()
        conn.close()
        if not rows:
            return None
        rows = list(reversed(rows))
        return [{
            'date': r[0], 'open': r[1], 'close': r[2],
            'high': r[3], 'low': r[4], 'volume': r[5]
        } for r in rows]
    except:
        conn.close()
        return None

def is_limit_up(close, prev_close):
    return close >= prev_close * 1.095

def is_yizi(open_p, limit_price):
    return abs(open_p - limit_price) / limit_price < 0.003 if limit_price > 0 else False

# ============================================================
# F2信号扫描（与three_funds_f2.py一致）
# ============================================================
def scan_f2_signals(codes=None, today_quotes=None):
    if codes is None:
        codes = ALL_CODES
    if today_quotes is None:
        today_quotes = tencent_quote_batch(codes)

    signals = {'f2': [], 'f2_plus': []}

    for code in codes:
        quote = today_quotes.get(code)
        if not quote:
            continue

        kline = get_kline(code, 30)
        if not kline or len(kline) < 25:
            continue

        cur_chg = quote['change_pct']
        cur_price = quote['price']

        # F2条件：涨幅7%-10%
        if cur_chg < 7 or cur_chg >= 10:
            continue

        last_20 = kline[-20:] if len(kline) >= 20 else kline
        if len(last_20) < 15:
            continue

        close_prices = [d['close'] for d in last_20[:-1]]
        if not close_prices:
            continue
        min_close = min(close_prices)
        max_close = max(close_prices)
        range_pct = (max_close - min_close) / min_close * 100 if min_close > 0 else 0

        if range_pct >= 10:
            continue

        volumes = [d['volume'] for d in last_20[:-1]]
        avg_vol = sum(volumes) / len(volumes) if volumes else 1
        today_vol = quote['volume']
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 1

        if vol_ratio < 1.5:
            continue

        signal = {
            'code': code,
            'name': quote['name'],
            'change_pct': cur_chg,
            'range_pct_20d': round(range_pct, 2),
            'vol_ratio': round(vol_ratio, 2),
            'price': cur_price,
            'sector': CODE_TO_SECTOR.get(code, {}).get('name', ''),
        }
        signals['f2'].append(signal)

        # F2+精筛
        yesterday_vol = last_20[-2]['volume'] if len(last_20) >= 2 else 0
        yesterday_vol_ratio = yesterday_vol / avg_vol if avg_vol > 0 else 1
        upper_shadow_pct = (quote['high'] - max(cur_price, quote['open'])) / prev_close * 100 if quote['prev_close'] > 0 else 0

        if range_pct < 5 and yesterday_vol_ratio < 0.5 and upper_shadow_pct < 1:
            signals['f2_plus'].append({
                **signal,
                'yesterday_vol_ratio': round(yesterday_vol_ratio, 2),
                'upper_shadow': round(upper_shadow_pct, 2),
            })

    return signals

# ============================================================
# 盘尾选股扫描（与three_funds_f2.py一致）
# ============================================================
def scan_weipan_picks(codes=None, today_quotes=None):
    if codes is None:
        codes = ALL_CODES
    if today_quotes is None:
        today_quotes = tencent_quote_batch(codes)

    picks = []

    for code in codes:
        quote = today_quotes.get(code)
        if not quote:
            continue

        cur_price = quote['price']
        prev_close = quote['prev_close']

        if not is_limit_up(cur_price, prev_close):
            continue

        limit_price = round(prev_close * 1.10, 2)

        if is_yizi(quote['open'], limit_price):
            continue

        kline = get_kline(code, 25)
        if not kline or len(kline) < 20:
            continue

        last_20 = kline[-20:-1]
        if len(last_20) < 15:
            continue

        close_prices = [d['close'] for d in last_20]
        min_c = min(close_prices)
        max_c = max(close_prices)
        range_pct = (max_c - min_c) / min_c * 100 if min_c > 0 else 0

        volumes = [d['volume'] for d in last_20]
        avg_vol = sum(volumes) / len(volumes) if volumes else 1
        vol_ratio = quote['volume'] / avg_vol if avg_vol > 0 else 1

        upper_shadow = (quote['high'] - cur_price) / cur_price * 100 if cur_price > 0 else 0

        score = 0
        if range_pct < 10:
            score += 40
        elif range_pct < 15:
            score += 20
        if vol_ratio < 1.0:
            score += 30
        elif vol_ratio < 1.5:
            score += 15
        if upper_shadow < 0.3:
            score += 30
        elif upper_shadow < 0.5:
            score += 20
        elif upper_shadow < 1.0:
            score += 10

        pick = {
            'code': code,
            'name': quote['name'],
            'price': cur_price,
            'change_pct': quote['change_pct'],
            'range_pct_20d': round(range_pct, 2),
            'vol_ratio': round(vol_ratio, 2),
            'upper_shadow': round(upper_shadow, 2),
            'score': score,
            'quality': '极硬' if score >= 80 else ('硬板' if score >= 60 else ('可关注' if score >= 40 else '一般')),
            'sector': CODE_TO_SECTOR.get(code, {}).get('name', ''),
        }
        picks.append(pick)

    picks.sort(key=lambda x: -x['score'])
    return picks

# ============================================================
# 状态管理
# ============================================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {'pushed_f2': {}, 'pushed_f2p': {}, 'pushed_weipan': {}, 'last_check': '', 'date': str(date.today())}

def save_state(state):
    if state.get('date') != str(date.today()):
        # 每日重置
        state = {'pushed_f2': {}, 'pushed_f2p': {}, 'pushed_weipan': {}, 'last_check': '', 'date': str(date.today())}
    state['date'] = str(date.today())
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ============================================================
# 预警检测
# ============================================================
def check_alerts(f2_signals, weipan_picks):
    """检测新信号，返回需要推送的（新F2, 新F2+, 新盘尾）"""
    state = load_state()
    now = datetime.now().strftime('%H:%M')

    new_f2 = []
    new_f2p = []
    new_wp = []

    # 检查新F2信号
    for s in f2_signals['f2']:
        key = f"{s['code']}"
        if key not in state.get('pushed_f2', {}):
            new_f2.append(s)
            state.setdefault('pushed_f2', {})[key] = {
                'first_seen': now,
                'change_pct': s['change_pct'],
            }

    # 检查新F2+信号
    for s in f2_signals['f2_plus']:
        key = f"{s['code']}"
        if key not in state.get('pushed_f2p', {}):
            new_f2p.append(s)
            state.setdefault('pushed_f2p', {})[key] = {
                'first_seen': now,
                'change_pct': s['change_pct'],
            }

    # 检查新盘尾信号（只推高质量 ≥60分）
    for p in weipan_picks:
        if p['score'] < 60:
            continue
        key = f"{p['code']}"
        if key not in state.get('pushed_weipan', {}):
            new_wp.append(p)
            state.setdefault('pushed_weipan', {})[key] = {
                'first_seen': now,
                'score': p['score'],
                'quality': p['quality'],
            }

    state['last_check'] = now
    save_state(state)

    return new_f2, new_f2p, new_wp

# ============================================================
# 格式化推送消息
# ============================================================
def format_push(new_f2, new_f2p, new_wp):
    """生成微信推送格式"""
    now = datetime.now().strftime('%H:%M')
    lines = []

    has_any = bool(new_f2 or new_f2p or new_wp)

    if not has_any:
        lines.append(f"✅ **F2盘中信号 | {now}**")
        lines.append("   无新增信号")
        return '\n'.join(lines)

    lines.append(f"🚨 **F2盘尾实时预警 | {now}**")
    lines.append("")

    if new_f2:
        lines.append(f"🔥 **F2信号（横盘首阳）{len(new_f2)}只**")
        for s in new_f2:
            lines.append(f"   {s['name']}({s['code']}) +{s['change_pct']:+.1f}% 前20振幅{s['range_pct_20d']}% 量比{s['vol_ratio']}倍")
        lines.append("   → T+5均+10.75% | 全部可买入")
        lines.append("")

    if new_f2p:
        lines.append(f"💎 **F2+精筛（最强信号）{len(new_f2p)}只**")
        for s in new_f2p:
            lines.append(f"   {s['name']}({s['code']}) +{s['change_pct']:+.1f}%")
        lines.append("   → T+5均+13.01% 暴利率42.6%")
        lines.append("")

    if new_wp:
        lines.append(f"🌅 **盘尾选股（硬板）{len(new_wp)}只**")
        for p in new_wp:
            icon = {'极硬': '✅', '硬板': '👍'}.get(p['quality'], '')
            lines.append(f"   {icon}{p['name']}({p['code']}) {p['score']}分 {p['quality']} 量比{p['vol_ratio']}")
        lines.append("   → 盘尾买入T+1均+2~4% 胜率58%+")
        lines.append("")

    lines.append("💡 操作建议：")
    if new_f2p:
        lines.append("   F2+出现→盘中可直接买")
    if new_f2:
        lines.append("   F2出现→关注后续是否加强为F2+")
    if new_wp:
        lines.append("   ⭐盘尾标的→14:55后收盘价买入")

    return '\n'.join(lines)

def format_status():
    """查看当前监控状态"""
    state = load_state()
    now = datetime.now().strftime('%H:%M:%S')

    lines = [f"📡 **F2实时监控状态 | {now}**", ""]
    lines.append(f"日期: {state.get('date', '未知')}")
    lines.append(f"上次检查: {state.get('last_check', '从未')}")
    lines.append("")

    f2_count = len(state.get('pushed_f2', {}))
    f2p_count = len(state.get('pushed_f2p', {}))
    wp_count = len(state.get('pushed_weipan', {}))

    lines.append(f"今日F2信号: {f2_count}只")
    if f2_count > 0:
        for code, info in state['pushed_f2'].items():
            lines.append(f"   {code} +{info.get('change_pct', 0):+.1f}% 首见{info.get('first_seen', '')}")

    lines.append(f"今日F2+信号: {f2p_count}只")
    if f2p_count > 0:
        for code, info in state['pushed_f2p'].items():
            lines.append(f"   {code} +{info.get('change_pct', 0):+.1f}% 首见{info.get('first_seen', '')}")

    lines.append(f"今日盘尾标的(硬板): {wp_count}只")
    if wp_count > 0:
        for code, info in state['pushed_weipan'].items():
            lines.append(f"   {code} {info.get('quality', '')} {info.get('score', 0)}分 首见{info.get('first_seen', '')}")

    return '\n'.join(lines)

# ============================================================
# Main
# ============================================================
def main():
    args = sys.argv[1:]
    status_mode = '--status' in args

    if status_mode:
        print(format_status())
        return

    # 非交易日跳过扫描（周六日/法定假日）
    from datetime import date
    wd = date.today().weekday()
    if wd >= 5:
        log(f"⏸️ 非交易日(周{'六日'[wd-5]}), 跳过扫描")
        print(json.dumps({'alerts': [], 'status': 'weekend', 'time': datetime.now().isoformat()}))
        return

    # 获取实时行情
    quotes = tencent_quote_batch(ALL_CODES)
    if not quotes:
        log("⚠️ 无法获取行情数据")
        print(json.dumps({'alerts': [], 'status': 'no_data', 'time': datetime.now().isoformat()}))
        return

    # 扫描信号
    f2_signals = scan_f2_signals(ALL_CODES, quotes)
    weipan_picks = scan_weipan_picks(ALL_CODES, quotes)

    # 检测新信号
    new_f2, new_f2p, new_wp = check_alerts(f2_signals, weipan_picks)

    # 输出
    push_msg = format_push(new_f2, new_f2p, new_wp)
    print(push_msg)

    if new_f2 or new_f2p or new_wp:
        log(f"🚨 新信号推送: F2={len(new_f2)}只, F2+={len(new_f2p)}只, 盘尾硬板={len(new_wp)}只")
    else:
        log(f"✅ 无新信号 (F2={len(f2_signals['f2'])}只, F2+={len(f2_signals['f2_plus'])}只, 盘尾={len(weipan_picks)}只)")

if __name__ == '__main__':
    main()
