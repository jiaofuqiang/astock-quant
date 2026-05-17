#!/usr/bin/env python3
"""可转债行情采集器
   采集10只活跃可转债（深交所5只+上交所5只）
   使用腾讯行情API：http://qt.gtimg.cn/q=sz{code},sh{code}
   输出JSON到 /home/ubuntu/V2board/data/cb_market.json
   格式: {timestamp, is_open, bonds: [{code, name, price, change_pct, premium_rate, underlying_stock}, ...]}
"""

import json, os, urllib.request
from datetime import datetime

# ---------- 活跃可转债代码（2026年5月选股） ----------
# 深交所5只 + 上交所5只
CB_CODES = [
    'sz123222',   # 博俊转债 - 博俊科技(300926) 汽车零部件
    'sz123223',   # 九典转02 - 九典制药(300705) 医药
    'sz127089',   # 晶澳转债 - 晶澳科技(002459) 光伏
    'sz127084',   # 柳工转2   - 柳工(000528) 工程机械
    'sz127095',   # 广泰转债 - 威海广泰(002111) 空港装备
    'sh113066',   # 平煤转债 - 平煤股份(601666) 煤炭
    'sh111000',   # 兴业转债 - 兴业银行(601166) 银行
    'sh113065',   # 中金转债 - 中金岭南(000060) 有色金属
    'sh113064',   # 常银转债 - 常熟银行(601128) 银行
    'sh113068',   # 福立转债 - 福立旺(688678) 精密制造
]

# 正股名称映射 (code -> {name, underlying})
CB_META = {
    'sz123222': {'name': '博俊转债', 'underlying': '博俊科技(300926)'},
    'sz123223': {'name': '九典转02', 'underlying': '九典制药(300705)'},
    'sz127089': {'name': '晶澳转债', 'underlying': '晶澳科技(002459)'},
    'sz127084': {'name': '柳工转2',  'underlying': '柳工(000528)'},
    'sz127095': {'name': '广泰转债', 'underlying': '威海广泰(002111)'},
    'sh113066': {'name': '平煤转债', 'underlying': '平煤股份(601666)'},
    'sh111000': {'name': '兴业转债', 'underlying': '兴业银行(601166)'},
    'sh113065': {'name': '中金转债', 'underlying': '中金岭南(000060)'},
    'sh113064': {'name': '常银转债', 'underlying': '常熟银行(601128)'},
    'sh113068': {'name': '福立转债', 'underlying': '福立旺(688678)'},
}


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val, default=''):
    if val is None:
        return default
    return str(val).strip()


def parse_tencent_cb_response(text):
    """解析腾讯行情响应文本（可转债格式）
    标准字段索引（可转债特有字段）:
      [1]  = 名称
      [2]  = 代码
      [3]  = 当前价
      [4]  = 昨收
      [32] = 涨跌幅(%)
      [62] = 转股溢价率(%)
      [61] = 'ZQ-KZZ' 表示可转债
    """
    results = []
    raw_items = text.strip().split(';')

    for raw_line in raw_items:
        line = raw_line.strip()
        if not line or '=' not in line:
            continue

        # 提取引号内的内容
        try:
            eq_idx = line.index('=')
            raw_value = line[eq_idx+1:].strip()
            if raw_value.startswith('"') and raw_value.endswith('"'):
                raw_value = raw_value[1:-1]
            elif raw_value.startswith('"'):
                raw_value = raw_value[1:]
            elif raw_value.endswith('"'):
                raw_value = raw_value[:-1]
        except (ValueError, IndexError):
            continue

        # 跳过无数据响应
        if raw_value in ('', '1', '0'):
            continue

        fields = raw_value.split('~')
        if len(fields) < 63:
            continue

        # 提取代码
        var_name = line[:eq_idx].strip()
        code_full = var_name
        if code_full.startswith('v_'):
            code_full = code_full[2:]

        name = safe_str(fields[1]) if len(fields) > 1 else ''
        code_in_fields = safe_str(fields[2]) if len(fields) > 2 else ''
        price = safe_float(fields[3]) if len(fields) > 3 else 0.0
        change_pct_str = safe_str(fields[32]) if len(fields) > 32 else ''
        change_pct = safe_float(change_pct_str.replace('%', '')) if change_pct_str else 0.0

        # 转股溢价率 field[62]
        premium_rate_str = safe_str(fields[62]) if len(fields) > 62 else ''
        premium_rate = safe_float(premium_rate_str) if premium_rate_str else 0.0

        # 获取正股名称
        meta = CB_META.get(code_full, {})
        display_name = meta.get('name', name or code_full)
        underlying = meta.get('underlying', '')

        # 市场类型检查
        market_type = safe_str(fields[61]) if len(fields) > 61 else ''
        if market_type != 'ZQ-KZZ':
            # 非可转债数据跳过
            continue

        # 溢价率>0表示溢价（溢价买入），<0表示折价（折价买入可转股套利）
        premium_rate_str_display = f"{premium_rate:+.2f}%" if premium_rate != 0 else "0.00%"

        item = {
            'code': code_in_fields or code_full,
            'code_full': code_full,
            'name': display_name,
            'price': round(price, 2),
            'change_pct': round(change_pct, 2),
            'premium_rate': round(premium_rate, 2),
            'premium_rate_str': premium_rate_str_display,
            'underlying_stock': underlying,
        }
        results.append(item)

    return results


def fetch_quotes():
    """从腾讯行情获取可转债实时数据"""
    codes_str = ','.join(CB_CODES)
    url = f'http://qt.gtimg.cn/q={codes_str}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        # 腾讯返回GBK编码
        text = str(raw, 'gbk')
    except Exception as e:
        raise RuntimeError(f"HTTP请求失败: {e}")

    return parse_tencent_cb_response(text)


def collect():
    """主采集函数"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    # 判断是否交易时段（可转债交易时间与A股一致: 9:30-11:30, 13:00-15:00）
    is_trade_day = weekday < 5  # 周一~周五
    is_trade_time = is_trade_day and (
        (hour == 9 and minute >= 30) or
        (10 <= hour <= 11) or
        (hour == 13) or
        (hour == 14) or
        (hour == 15 and minute == 0)
    )
    is_bidding_time = is_trade_day and (hour == 9 and 15 <= minute <= 29)

    market_period = 'afterhours'
    if is_bidding_time:
        market_period = 'bidding'
    elif is_trade_time:
        market_period = 'trade'

    try:
        quotes = fetch_quotes()
    except Exception as e:
        return {
            'timestamp': timestamp,
            'market_period': market_period,
            'is_open': is_bidding_time or is_trade_time,
            'error': str(e),
            'bonds': [],
            'count': 0,
        }

    return {
        'timestamp': timestamp,
        'market_period': market_period,
        'is_open': is_bidding_time or is_trade_time,
        'bonds': quotes,
        'count': len(quotes),
    }


def main():
    output_dir = os.path.expanduser('/home/ubuntu/V2board/data')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'cb_market.json')

    result = collect()

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    ts = result['timestamp']
    count = result['count']
    period = result['market_period']
    is_open = result['is_open']
    error = result.get('error')
    if error:
        print(f"[cb_market] {ts} | 周期={period} | 错误: {error}", flush=True)
    else:
        print(f"[cb_market] {ts} | 周期={period} | 在交易时段={is_open} | 采集{count}只可转债", flush=True)


if __name__ == '__main__':
    main()
