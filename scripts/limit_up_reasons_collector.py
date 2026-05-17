#!/usr/bin/env python3
"""
📊 涨停原因自动化采集器 v1.0
================================
基于东方财富 datacenter API + 腾讯行情 fallback

API来源：
  1. 东财 RPT_PCHOT_LIMITLIST_HSDETIAL — 涨停原因+内容+板块（主数据源）
  2. 东财 RPT_INTSELECTION_LIMITSTOCKHIS — 全量涨停监控（封单/板数/炸板）
  3. 腾讯 qt.gtimg.cn — fallback（仅作验证，不含原因字段）

输出：
  ~/astock/data/limit_reasons_latest.json  ← dashboard_aggregator.py 直接读取注入bundle
  格式兼容现有 {limit_reasons, limit_list_full, stats}

用法：
  python3 scripts/limit_up_reasons_collector.py              # 采集今天
  python3 scripts/limit_up_reasons_collector.py --date=2026-05-15   # 指定日期
  python3 scripts/limit_up_reasons_collector.py --check      # 校验已有数据
"""

import os, sys, json, subprocess, time
from datetime import datetime, date, timedelta
import urllib.parse

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
OUTPUT_FILE = os.path.join(DATA_DIR, 'limit_reasons_latest.json')

# ──────────────────────────────────────────
# 东财 API 常量
# ──────────────────────────────────────────
API_DATACENTER = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
API_SECURITIES = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'

# API1: 涨停原因明细（含AI解析原因+内容）
REPORT_REASONS = 'RPT_PCHOT_LIMITLIST_HSDETIAL'
COLUMNS_REASONS = 'SECURITY_CODE,SECURITY_NAME_ABBR,LIMIT_REASON,LIMIT_CONTENT,BOARD_NAME,BOARD_CODE'

# API2: 全量涨停监控（封单/板数/炸板等）
REPORT_LIMITS = 'RPT_INTSELECTION_LIMITSTOCKHIS'
COLUMNS_LIMITS = 'ALL'

PAGE_SIZE = 5000


def sf(v, default=0):
    """safe float"""
    try:
        return float(v) if v is not None and v != '-' else default
    except (ValueError, TypeError):
        return default


def curl_get(url, timeout=10):
    """执行curl请求，返回 decoded text"""
    r = subprocess.run(
        ['curl', '-s', '--connect-timeout', '5', '--max-time', str(timeout), url],
        capture_output=True, timeout=timeout + 5
    )
    return r.stdout.decode('utf-8', errors='replace')


def fetch_limit_reasons(trade_date, page=1):
    """API1: 获取涨停原因（AI解析）"""
    dt_filter = f"(TRADE_DATE='{trade_date} 00:00:00')"
    encoded_filter = urllib.parse.quote(dt_filter, safe='()=')

    url = (
        f'{API_SECURITIES}?source=SECURITIES&client=APP'
        f'&reportName={REPORT_REASONS}'
        f'&columns={COLUMNS_REASONS}'
        f'&filter={encoded_filter}'
        f'&pageNumber={page}&pageSize={PAGE_SIZE}'
        f'&sortColumns=RANK_TIME&sortTypes=-1'
    )
    raw = curl_get(url, timeout=10)
    if not raw:
        return None
    d = json.loads(raw)
    return d.get('result', {})


def fetch_limit_list(trade_date, page=1):
    """API2: 全量涨停监控（封单/板数/涨停方式）"""
    encoded_date = urllib.parse.quote(trade_date, safe='')
    url = (
        f'{API_DATACENTER}?reportName={REPORT_LIMITS}&columns={COLUMNS_LIMITS}'
        f'&pageNumber={page}&pageSize={PAGE_SIZE}'
        f'&sortColumns=CLOSE_LIMITUP_TIME&sortTypes=1'
        f'&filter=(TRADE_DATE%3D%27{trade_date}%27)'
    )
    raw = curl_get(url, timeout=10)
    if not raw:
        return None
    d = json.loads(raw)
    return d.get('result', {})


def collect_date(trade_date):
    """
    采集指定日期的涨停原因数据，返回结构化 dict。
    格式兼容 bundle 的 limit_reasons 字段。
    """
    result = {
        'limit_reasons': [],
        'limit_list_full': [],
        'stats': {'total': 0, 'yizi': 0, 'ziran': 0},
    }

    # ========== API1: 涨停原因 ==========
    print(f'  [API1] RPT_PCHOT_LIMITLIST_HSDETIAL → 涨停原因...', end=' ')
    try:
        page = 1
        reasons_all = []
        while True:
            res = fetch_limit_reasons(trade_date, page)
            if not res:
                break
            data = res.get('data', [])
            if not data:
                break
            reasons_all.extend(data)
            pages = res.get('pages', 0)
            if page >= pages:
                break
            page += 1
            time.sleep(0.15)

        print(f'{len(reasons_all)}条')
        for r in reasons_all:
            result['limit_reasons'].append({
                'SECURITY_CODE': r.get('SECURITY_CODE', ''),
                'SECURITY_NAME_ABBR': r.get('SECURITY_NAME_ABBR', ''),
                'LIMIT_REASON': r.get('LIMIT_REASON', ''),
                'LIMIT_CONTENT': r.get('LIMIT_CONTENT', ''),
                'BOARD_NAME': r.get('BOARD_NAME', ''),
                'BOARD_CODE': r.get('BOARD_CODE', ''),
            })
    except Exception as e:
        print(f'❌ 失败: {e}')

    # ========== API2: 全量涨停监控 ==========
    print(f'  [API2] RPT_INTSELECTION_LIMITSTOCKHIS → 全量监控...', end=' ')
    try:
        page = 1
        limits_all = []
        while True:
            res = fetch_limit_list(trade_date, page)
            if not res:
                break
            data = res.get('data', [])
            if not data:
                break
            limits_all.extend(data)
            pages = res.get('pages', 0)
            if page >= pages:
                break
            page += 1
            time.sleep(0.15)

        print(f'{len(limits_all)}条')
        for r in limits_all:
            nlimite = r.get('NDAYS_NLIMITE', '今日首板')
            limit_way = r.get('LIMIT_WAY', '自然涨停')
            board_count = 1
            if nlimite and '天' in nlimite and '板' in nlimite:
                parts = nlimite.replace('板', '').split('天')
                if len(parts) == 2:
                    board_count = int(parts[1])

            result['limit_list_full'].append({
                'code': r.get('SECURITY_CODE', ''),
                'name': r.get('SECURITY_NAME_ABBR', ''),
                'board_desc': nlimite,
                'board_count': board_count,
                'limit_way': limit_way,
                'close_time': r.get('CLOSE_LIMITUP_TIME', ''),
                'seal_wan': sf(r.get('LAST_LIMITUP_NUM_NEW', 0)),
                'turnover_rate': sf(r.get('TURNOVERRATE', 0)),
                'net_inflow': sf(r.get('NET_INFLOW', 0)),
                'open_times': int(r.get('OPEN_TIMES', 0) or 0),
                'fbl': sf(r.get('FBL', 0)),
                'board_name': r.get('BOARD_NAME', ''),
                'industry': r.get('INDUSTRY', ''),
                'yield_pct': sf(r.get('YIELD', 0)),
            })

        total = len(limits_all)
        yizi = sum(1 for r in limits_all if r.get('LIMIT_WAY') == '一字涨停')
        result['stats'] = {
            'total': total,
            'yizi': yizi,
            'ziran': total - yizi,
        }
    except Exception as e:
        print(f'❌ 失败: {e}')

    return result


def try_tencent_fallback(trade_date, limit_codes):
    """
    Fallback: 尝试腾讯行情获取涨停数据（仅做验证，不含原因字段）。
    返回 dict 或 None
    """
    if not limit_codes:
        return None

    def mkt(code):
        return f'sh{code}' if code[0] in ('6', '5', '9') else f'sz{code}'

    result = {'limit_reasons': [], 'limit_list_full': [], 'stats': {'total': 0, 'yizi': 0, 'ziran': 0}}

    for i in range(0, len(limit_codes), 50):
        batch = limit_codes[i:i + 50]
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, timeout=10
            )
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if '=' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 48:
                    continue
                code = parts[2].strip()
                name = parts[1].strip()
                cur = sf(parts[3])
                prev = sf(parts[4])
                chg = sf(parts[32])
                if prev <= 0:
                    continue

                # 判断涨停
                is_limit = chg >= 9.5 and cur >= prev * 1.09
                if not is_limit:
                    continue

                open_p = sf(parts[5])
                is_yizi = open_p >= prev * 1.095
                seal_buy1 = sf(parts[9])
                seal_vol = sf(parts[10])
                seal_wan = seal_buy1 * seal_vol / 10000 if seal_buy1 > 0 and seal_vol > 0 else 0

                result['limit_list_full'].append({
                    'code': code,
                    'name': name,
                    'board_desc': '腾讯fallback',
                    'board_count': 1,
                    'limit_way': '一字涨停' if is_yizi else '自然涨停',
                    'close_time': '',
                    'seal_wan': round(seal_wan, 1),
                    'turnover_rate': sf(parts.get(38, 0)),
                    'net_inflow': 0,
                    'open_times': 0,
                    'fbl': 0,
                    'board_name': '',
                    'industry': '',
                    'yield_pct': 0,
                })
                result['limit_reasons'].append({
                    'SECURITY_CODE': code,
                    'SECURITY_NAME_ABBR': name,
                    'LIMIT_REASON': '(腾讯行情无原因字段)',
                    'LIMIT_CONTENT': '',
                    'BOARD_NAME': '',
                    'BOARD_CODE': '',
                })
        except Exception:
            continue

    result['stats']['total'] = len(result['limit_reasons'])
    result['stats']['yizi'] = sum(1 for r in result['limit_reasons'])
    result['stats']['ziran'] = 0
    return result


def collect(trade_date=None):
    """主采集入口"""
    if trade_date is None:
        trade_date = str(date.today())

    print(f'\n📊 涨停原因采集 — {trade_date}')
    print(f'{"=" * 45}')

    # 主数据源：东财API
    result = collect_date(trade_date)

    if result['limit_reasons'] or result['limit_list_full']:
        print(f'  ✅ 东财API: {result["stats"]["total"]}只涨停, '
              f'{result["stats"]["yizi"]}只一字, {result["stats"]["ziran"]}只自然')
    else:
        print(f'  ⚠️ 东财API返回空，尝试腾讯行情fallback...')
        # 尝试从 existing limit_up_collector_v2 DB 获取今日涨停代码
        limit_codes = []
        try:
            LIMIT_DB = os.path.join(DATA_DIR, 'daily_limit_data.db')
            r = subprocess.run(
                ['sqlite3', '-noheader', LIMIT_DB,
                 f"SELECT code FROM limit_stocks_v2 WHERE date='{trade_date}'"],
                capture_output=True, text=True, timeout=10
            )
            limit_codes = [c.strip() for c in r.stdout.strip().split('\n') if c.strip()]
        except Exception:
            pass

        fallback = try_tencent_fallback(trade_date, limit_codes[:50])
        if fallback and fallback['limit_reasons']:
            result = fallback
            print(f'  ✅ 腾讯fallback: {result["stats"]["total"]}只涨停')
        else:
            print(f'  ❌ 所有API均失败，生成空JSON')
            result = {
                'limit_reasons': [],
                'limit_list_full': [],
                'stats': {'total': 0, 'yizi': 0, 'ziran': 0},
            }

    # 写入输出文件
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'  💾 写入 {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)} bytes)')
    print(f'  📊 合计: 涨停原因{len(result["limit_reasons"])}条, '
          f'全量监控{len(result["limit_list_full"])}条')

    return result


def check():
    """校验已有数据"""
    if not os.path.exists(OUTPUT_FILE):
        print(f'❌ 数据文件不存在: {OUTPUT_FILE}')
        return

    with open(OUTPUT_FILE, encoding='utf-8') as f:
        data = json.load(f)

    print(f'\n📋 涨停原因数据校验')
    print(f'{"=" * 45}')
    print(f'  文件: {OUTPUT_FILE}')
    print(f'  大小: {os.path.getsize(OUTPUT_FILE)} bytes')
    print(f'  涨停原因: {len(data.get("limit_reasons", []))}条')
    print(f'  全量监控: {len(data.get("limit_list_full", []))}条')
    stats = data.get('stats', {})
    print(f'  统计: 共{stats.get("total", 0)}只, '
          f'一字{stats.get("yizi", 0)}, 自然{stats.get("ziran", 0)}')

    # 示例展示前3条
    reasons = data.get('limit_reasons', [])
    if reasons:
        print(f'\n  前3条涨停原因:')
        for i, r in enumerate(reasons[:3]):
            print(f'    {i + 1}. {r.get("SECURITY_CODE", "")} '
                  f'{r.get("SECURITY_NAME_ABBR", "")} '
                  f'→ {r.get("LIMIT_REASON", "")[:60]}'
                  f' [{r.get("BOARD_NAME", "")}]')


def install_cron():
    """安装收盘后cron任务（15:35，在market_daily_integrator之前）"""
    script_path = os.path.abspath(__file__)
    cron_line = f"35 15 * * 1-5 cd {BASE} && python3 {script_path} >> {BASE}/logs/limit_up_reasons_collector.log 2>&1"

    import subprocess as sp
    result = sp.run(['crontab', '-l'], capture_output=True, text=True)
    existing = result.stdout

    if 'limit_up_reasons_collector' in existing:
        print("⚠️ cron任务已存在")
        return

    new_cron = existing.strip() + '\n' + cron_line + '\n'
    sp.run(['crontab'], input=new_cron, text=True)
    print(f"✅ cron已添加: {cron_line}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='涨停原因自动化采集器 v1.0')
    parser.add_argument('--date', help='指定日期 YYYY-MM-DD')
    parser.add_argument('--check', action='store_true', help='校验已有数据')
    parser.add_argument('--install-cron', action='store_true', help='安装收盘后cron任务(15:35)')
    args = parser.parse_args()

    if args.install_cron:
        install_cron()
    elif args.check:
        check()
    else:
        trade_date = args.date if args.date else str(date.today())
        collect(trade_date)
