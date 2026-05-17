#!/usr/bin/env python3
"""
📡 外部涨停原因聚合 v1.0

从多个外部平台采集涨停原因，与自有涨停归因引擎交叉验证。

数据源：
1. 东方财富涨停板池API（已有 push2.eastmoney.com）
2. 韭研公社个股讨论（browser采集）
3. 东方财富个股公告（browser采集）

输出：~/astock/data/ban_reasons.json
      ~/astock/data/ban_reasons_merged.txt（供limit_up_attribution使用）

用法：
  python3 scripts/fetch_ban_reasons.py                  # 采集今日
  python3 scripts/fetch_ban_reasons.py --date 2026-05-08  # 指定日期
  python3 scripts/fetch_ban_reasons.py --merge          # 与自有引擎合并
  python3 scripts/fetch_ban_reasons.py --push           # 推送到微信
"""
import os, sys, json, subprocess, re, sqlite3
from datetime import datetime, date, timedelta
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")
OUTPUT_FILE = os.path.join(BASE, "data", "ban_reasons.json")
MERGED_FILE = os.path.join(BASE, "data", "ban_reasons_merged.txt")
KLINE_DB = os.path.join(DATA_DIR, "kline_cache.db")

# ============================================================
# 板块成分股（核心标的）
# ============================================================
CORE_CODES = [
    '603986','603019','600584','603005','603160','002049','600171',
    '603893','002185','601138','000977','600498','000063','002916',
    '300308','688041','002472','002896','300124','688160','300660',
    '688017','300580','601689','603662','300624','002230','300418',
    '603533','002555','002085','600580','300177','000099','600391',
    '300750','002074','300014','002460','002709','600884','002812',
    '300769',
]


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)


def curl_get(url, timeout=10):
    try:
        r = subprocess.run(['curl', '-s', '-H', 'User-Agent: Mozilla/5.0',
                            '--connect-timeout', '5', '--max-time', str(timeout), url],
                           capture_output=True, timeout=timeout+5)
        return r.stdout.decode('utf-8', errors='replace')
    except:
        return None


# ============================================================
# 数据源1: 东方财富涨停板池
# ============================================================
def fetch_em_limit_up(trade_date=None):
    """
    从东方财富涨停板池获取今日涨停数据
    返回 [{'code':..., 'name':..., 'chg':..., 'fengdan':..., 'turnover':..., 'concepts': [...]}]
    """
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1"
           "&fltt=2&invt=2&fid=f3"
           "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
           "&fields=f2,f3,f4,f12,f14,f15,f16,f17,f18,f170,f171,f172,f173")
    
    raw = curl_get(url, timeout=8)
    if not raw:
        return [], "API无响应"
    
    try:
        data = json.loads(raw)
        items = data.get('data', {}).get('diff', [])
    except:
        return [], "JSON解析失败"
    
    stocks = []
    for item in items:
        code = str(item.get('f12', ''))
        chg = item.get('f3', 0)
        name = item.get('f14', '')
        fengdan = item.get('f170', 0)
        
        # 只保留涨停>9.5%的
        if chg < 9.5:
            continue
        
        stocks.append({
            'code': code,
            'name': name,
            'change_pct': chg,
            'fengdan': fengdan,
            'is_core': code in CORE_CODES,
        })
    
    return stocks, None


# ============================================================
# 数据源2: 韭研公社个股讨论（涨停原因找法）
# ============================================================
def fetch_jiuyuan_reason(code, name):
    """
    通过韭研公社搜索个股，获取讨论帖中的核心逻辑
    返回 原因文本 或 None
    """
    # 先试试直接搜个股页面
    url = f"https://www.jiuyangongshe.com/plan?pageType=search&stock_name={name}"
    raw = curl_get(url, timeout=10)
    if not raw:
        return None
    
    # 试试直接从讨论中提取
    reasons = []
    # 找"看好"后面跟的原因文本
    patterns = [
        r'看好[^。]*。[^。]*',
        r'核心逻辑[：:]\s*([^。]+)',
        r'(涨停|大涨)原因[：:]\s*([^。<]+)',
    ]
    for pat in patterns:
        matches = re.findall(pat, raw)
        for m in matches[:2]:
            if isinstance(m, tuple):
                m = ''.join(m)
            if len(m) > 10 and len(m) < 200:
                reasons.append(m.strip())
    
    return '; '.join(reasons[:2]) if reasons else None


# ============================================================
# 数据源3: 查询东方财富个股F10-主营概念
# ============================================================
def fetch_stock_concepts(code):
    """从东方财富批量获取个股概念"""
    url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_INDUSTRY_CHAIN&client=ALL&pageNumber=1&pageSize=10&sortTypes=-1&sortColumns=NOTICE_DATE&source=HSF&client=ALL&filter=(SECURITY_CODE=%22{code}%22)"
    raw = curl_get(url, timeout=8)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        items = data.get('result', {}).get('data', [])
        concepts = []
        for item in items:
            name = item.get('INDUSTRY_CHAIN_NAME', '')
            if name:
                concepts.append(name)
        return concepts[:5]
    except:
        return []


# ============================================================
# 主流程
# ============================================================
def collect_all(trade_date=None):
    """聚合所有外部涨停原因"""
    if trade_date is None:
        trade_date = date.today().isoformat()
    
    log(f"📡 外部涨停原因聚合: {trade_date}")
    
    # 1. 获取涨停板池
    limit_up_stocks, err = fetch_em_limit_up(trade_date)
    if err or not limit_up_stocks:
        log(f"⚠️ 涨停板池: {err or '无数据'}")
        limit_up_stocks = []
    else:
        log(f"  涨停板池: {len(limit_up_stocks)}只涨停")
    
    # 2. 合并结果
    results = []
    for s in limit_up_stocks:
        code = s['code']
        name = s['name']
        
        entry = {
            'code': code,
            'name': name,
            'change_pct': s['change_pct'],
            'fengdan': s['fengdan'],
            'is_core': s['is_core'],
            'sources': {},
            'merged_reason': '',
        }
        
        # 从东方财富获取概念
        concepts = fetch_stock_concepts(code)
        entry['sources']['em_concepts'] = concepts
        
        results.append(entry)
    
    # 3. 对核心标的，额外查韭研公社
    core_results = [r for r in results if r['is_core']]
    for r in core_results[:5]:  # 只查前5只核心票，避免太慢
        reason = fetch_jiuyuan_reason(r['code'], r['name'])
        if reason:
            r['sources']['jiuyuan'] = reason
    
    # 4. 生成合并原因
    for r in results:
        parts = []
        concepts = r['sources'].get('em_concepts', [])
        if concepts:
            parts.append(f"概念:{','.join(concepts[:3])}")
        jy = r['sources'].get('jiuyuan', '')
        if jy:
            parts.append(f"讨论:{jy[:60]}")
        r['merged_reason'] = ' | '.join(parts)
    
    # 5. 保存
    output = {
        'date': trade_date,
        'timestamp': datetime.now().isoformat(),
        'total': len(results),
        'core_count': len(core_results),
        'stocks': results,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    log(f"✅ 外部涨停原因聚合完成: {len(results)}只")
    
    return output


def merge_with_local(trade_date):
    """与自有涨停归因引擎合并"""
    # 读取外部数据
    if not os.path.exists(OUTPUT_FILE):
        return None
    
    with open(OUTPUT_FILE) as f:
        external = json.load(f)
    
    # 调用limit_up_attribution获取自有引擎的涨停归因
    r = subprocess.run(
        ['python3', f'{BASE}/scripts/limit_up_attribution.py', '--date', trade_date, '--json'],
        capture_output=True, timeout=60, text=True, cwd=BASE
    )
    
    if r.returncode != 0 or not r.stdout:
        log("⚠️ 自有引擎无输出")
        return external
    
    # 合并
    try:
        local_data = json.loads(r.stdout)
    except:
        local_data = None
    
    if local_data:
        for s in external.get('stocks', []):
            code = s['code']
            for ls in (local_data.get('stocks', []) if isinstance(local_data, dict) else []):
                if ls.get('code') == code:
                    s['local_reason'] = ls.get('reason', '')
                    s['local_concept'] = ls.get('concept', '')
                    break
    
    # 输出合并结果
    lines = []
    lines.append(f"📡 涨停原因合并 | {trade_date}")
    lines.append("=" * 60)
    
    for s in external.get('stocks', [])[:15]:
        if not s.get('is_core') and s.get('change_pct', 0) < 9.9:
            continue
        fengdan_str = f"封单{s['fengdan']:.0f}万" if s['fengdan'] else ""
        lines.append(f"  {s['name']:<8s} +{s['change_pct']:.1f}% {fengdan_str}")
        ext_reason = s.get('merged_reason', '')
        if ext_reason:
            lines.append(f"    外部: {ext_reason[:100]}")
        local = s.get('local_reason', '') or s.get('local_concept', '')
        if local:
            lines.append(f"    自有: {local[:100]}")
        lines.append("")
    
    with open(MERGED_FILE, 'w') as f:
        f.write('\n'.join(lines))
    
    log(f"✅ 合并完成: {MERGED_FILE}")
    
    # 输出差异（外部有但自有没有的）
    diff_count = 0
    for s in external.get('stocks', []):
        if not s.get('local_reason') and s.get('merged_reason'):
            diff_count += 1
    if diff_count > 0:
        log(f"⚠️ 外部有但自有引擎未覆盖: {diff_count}只")
    
    return external


def format_push(external):
    """生成微信推送"""
    if not external or not external.get('stocks'):
        return ""
    
    lines = []
    lines.append(f"📡 **涨停原因聚合 | {external['date']}**")
    lines.append("")
    
    # 核心标的
    core = [s for s in external['stocks'] if s.get('is_core')]
    if core:
        lines.append(f"**核心标的涨停 ({len(core)}只)**")
        for s in core:
            fengdan = f"封{s['fengdan']:.0f}万" if s.get('fengdan') else ""
            lines.append(f"  {s['name']}({s['code']}) +{s['change_pct']:.1f}% {fengdan}")
            if s.get('merged_reason'):
                lines.append(f"    {s['merged_reason'][:80]}")
        lines.append("")
    
    # 封单最大的5只
    by_fengdan = sorted([s for s in external['stocks'] if s.get('fengdan', 0) > 0], 
                        key=lambda x: -x['fengdan'])[:5]
    if by_fengdan:
        lines.append("**💰 封单最大**")
        for s in by_fengdan:
            lines.append(f"  {s['name']} 封单{s['fengdan']:.0f}万 +{s['change_pct']:.1f}%")
        lines.append("")
    
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='📡 外部涨停原因聚合')
    parser.add_argument('--date', help='日期 YYYY-MM-DD')
    parser.add_argument('--merge', action='store_true', help='与自有引擎合并')
    parser.add_argument('--push', action='store_true', help='推送微信')
    args = parser.parse_args()
    
    trade_date = args.date or date.today().isoformat()
    
    external = collect_all(trade_date)
    
    if args.merge and external:
        merge_with_local(trade_date)
    
    if args.push:
        push_msg = format_push(external)
        if push_msg:
            print("\n" + push_msg)
    
    # 输出摘要
    if external:
        print(f"\n📊 外部涨停原因聚合: {external['date']}")
        print(f"  涨停: {external['total']}只, 核心标的: {external['core_count']}只")
        for s in external['stocks'][:5]:
            print(f"  {s['name']:<8s} +{s['change_pct']:.1f}% {s.get('merged_reason','')[:60]}")
    
    # 非交易日则提示
    if not external or not external.get('stocks'):
        log("ℹ️ 无涨停数据（非交易日正常）")


if __name__ == '__main__':
    main()
