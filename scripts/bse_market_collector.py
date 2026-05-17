#!/usr/bin/env python3
"""北交所实时行情采集器
   采集10只成交活跃的北交所个股，输出JSON到 /home/ubuntu/V2board/data/bse_market.json
   使用腾讯行情API：http://qt.gtimg.cn/q=bj{code}
   北交所前缀 bj，报文格式与沪深一致（71/78字段）"""

import json, os, urllib.request
from datetime import datetime

# ---------- 北交所成交活跃个股（2026年5月选股） ----------
BSE_CODES = [
    'bj830799',   # 吉林碳谷 — 北交所碳纤维龙头
    'bj832149',   # 利尔达 — 半导体/物联网
    'bj833171',   # 国航远洋 — 航运
    'bj835185',   # 贝特瑞 — 北交所新能源电池材料龙头
    'bj836077',   # 吉林碳谷 — 
    'bj837092',   # 汉鑫科技 — 人工智能
    'bj838670',   # 恒拓开源 — 软件服务
    'bj839273',   # 一致魔芋 — 消费食品
    'bj839946',   # 华阳变速 — 汽车零部件
    'bj834599',   # 同力股份 — 北交所重卡矿用车龙头
]

CODE_TO_NAME = {
    'bj830799': '吉林碳谷',
    'bj832149': '利尔达',
    'bj833171': '国航远洋',
    'bj835185': '贝特瑞',
    'bj836077': '吉林碳谷',
    'bj837092': '汉鑫科技',
    'bj838670': '恒拓开源',
    'bj839273': '一致魔芋',
    'bj839946': '华阳变速',
    'bj834599': '同力股份',
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


def parse_tencent_response(text):
    """解析腾讯行情响应文本（北交所格式与沪深一致）
    标准字段索引:
      [1] = 名称
      [2] = 代码
      [3] = 当前价
      [4] = 昨收
      [5] = 开盘价
      [6] = 成交量(手)
      [7] = 成交额
      [8] = 最高价
      [9] = 最低价
      [31] = 涨跌额
      [32] = 涨跌幅(%)
      [43] = 日期
      [44] = 时间
      [45] = 涨跌停状态
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
        if len(fields) < 35:
            continue

        # 提取代码
        var_name = line[:eq_idx].strip()
        code_full = var_name
        if code_full.startswith('v_'):
            code_full = code_full[2:]

        name = safe_str(fields[1]) if len(fields) > 1 else ''
        code_in_fields = safe_str(fields[2]) if len(fields) > 2 else ''
        price = safe_float(fields[3]) if len(fields) > 3 else 0.0
        prev_close = safe_float(fields[4]) if len(fields) > 4 else 0.0
        open_price = safe_float(fields[5]) if len(fields) > 5 else 0.0
        volume = safe_float(fields[6]) if len(fields) > 6 else 0.0
        amount = safe_float(fields[7]) if len(fields) > 7 else 0.0
        high = safe_float(fields[8]) if len(fields) > 8 else 0.0
        low = safe_float(fields[9]) if len(fields) > 9 else 0.0
        change = safe_float(fields[31]) if len(fields) > 31 else 0.0
        change_pct_str = safe_str(fields[32]) if len(fields) > 32 else ''
        change_pct = safe_float(change_pct_str.replace('%', '')) if change_pct_str else 0.0
        date_str = safe_str(fields[43]) if len(fields) > 43 else ''
        time_str = safe_str(fields[44]) if len(fields) > 44 else ''
        status = safe_str(fields[45]) if len(fields) > 45 else ''

        # 安全fallback: 如果change为空但可以计算
        if change == 0.0 and price != 0.0 and prev_close != 0.0:
            change = round(price - prev_close, 4)
            change_pct = round((change / prev_close) * 100, 2)

        # 确定中文名
        display_name = name if name else CODE_TO_NAME.get(code_full, code_in_fields or code_full)

        item = {
            'code': code_in_fields or code_full,
            'code_full': code_full,
            'name': display_name,
            'price': round(price, 4),
            'prev_close': round(prev_close, 4),
            'open': round(open_price, 4),
            'high': round(high, 4),
            'low': round(low, 4),
            'change': round(change, 4),
            'change_pct': round(change_pct, 2),
            'volume': volume,
            'amount': amount,
            'date': date_str,
            'time': time_str,
            'status': status,
        }
        results.append(item)

    return results


def fetch_quotes():
    """从腾讯行情获取北交所实时数据"""
    codes_str = ','.join(BSE_CODES)
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

    return parse_tencent_response(text)


def collect():
    """主采集函数"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    # 判断是否交易时段
    is_trade_day = weekday < 5  # 周一~周五
    # 北交所交易时间: 9:30-11:30, 13:00-15:00
    is_trade_time = is_trade_day and (
        (hour == 9 and minute >= 30) or
        (10 <= hour <= 11) or
        (hour == 13) or
        (hour == 14) or
        (hour == 15 and minute == 0)
    )
    # 竞价时段 9:15-9:30（北交所也支持集合竞价）
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
            'stocks': [],
            'count': 0,
        }

    return {
        'timestamp': timestamp,
        'market_period': market_period,
        'is_open': is_bidding_time or is_trade_time,
        'stocks': quotes,
        'count': len(quotes),
    }


def main():
    output_dir = os.path.expanduser('/home/ubuntu/V2board/data')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'bse_market.json')

    result = collect()

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    ts = result['timestamp']
    count = result['count']
    period = result['market_period']
    is_open = result['is_open']
    error = result.get('error')
    if error:
        print(f"[bse_market] {ts} | 周期={period} | 错误: {error}", flush=True)
    else:
        print(f"[bse_market] {ts} | 周期={period} | 在交易时段={is_open} | 采集{count}只北交所个股", flush=True)


if __name__ == '__main__':
    main()
