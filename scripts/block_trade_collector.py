#!/usr/bin/env python3
"""大宗交易折溢价采集器
从东方财富datacenter API获取大宗交易明细数据
输出：TOP10折价率最高/溢价率最高的大宗交易 + 市场统计摘要
"""
import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)

OUTPUT_PATH = os.path.join(DATA_DIR, 'block_trade.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://data.eastmoney.com/dzjy/',
}

# Column mapping for RPT_DATA_BLOCKTRADE (个股大宗交易明细)
COLUMN_MAP_DETAIL = {
    'SECUCODE': 'SECUCODE',
    'SECURITY_CODE': 'CODE',
    'SECURITY_NAME_ABBR': 'NAME',
    'TRADE_DATE': 'TRADE_DATE',
    'DEAL_PRICE': 'DEAL_PRICE',
    'DEAL_VOLUME': 'VOLUME',
    'DEAL_AMT': 'AMOUNT',
    'PREMIUM_RATIO': 'PREMIUM_RATIO',
    'CLOSE_PRICE': 'CLOSE_PRICE',
    'CHANGE_RATE': 'CHANGE_RATE',
    'BUYER_NAME': 'BUYER',
    'SELLER_NAME': 'SELLER',
    'TURNOVER_RATE': 'TURNOVER_RATE',
    'TRADE_UNIT': 'TRADE_UNIT',
    'SECURITY_TYPE': 'SECURITY_TYPE',
}

# Column mapping for RPT_BLOCKTRADE_ACSTA (个股大宗交易统计 - 含D1/D5/D10收益)
COLUMN_MAP_STATS = {
    'SECUCODE': 'SECUCODE',
    'SECURITY_CODE': 'CODE',
    'SECURITY_NAME_ABBR': 'NAME',
    'TRADE_DATE': 'TRADE_DATE',
    'DEAL_NUM': 'DEAL_COUNT',
    'DEAL_AMT': 'TOTAL_AMOUNT',
    'DEAL_VOLUME_AMT': 'TOTAL_VOLUME',
    'PREMIUM_RATIO': 'AVG_PREMIUM_RATIO',
    'CHANGE_RATE': 'CHANGE_RATE',
    'CLOSE_PRICE': 'CLOSE_PRICE',
    'D1_AVG_ADJCHRATE': 'D1_AVG_CHANGE',
    'D5_AVG_ADJCHRATE': 'D5_AVG_CHANGE',
    'D10_AVG_ADJCHRATE': 'D10_AVG_CHANGE',
    'D20_AVG_ADJCHRATE': 'D20_AVG_CHANGE',
    'SUM_TURNOVERRATE': 'SUM_TURNOVER_RATE',
    'PREMIUM_TIMES': 'PREMIUM_TIMES',
    'DISCOUNT_TIMES': 'DISCOUNT_TIMES',
}


def fetch_data(report_name, filter_str, page_size=5000, sort_columns='TRADE_DATE', sort_types='-1'):
    """从东方财富datacenter API获取数据"""
    # URL编码：括号、大于号、单引号都需要手动编码
    # ( -> %28, ) -> %29, > -> %3E, ' -> %27
    filter_encoded = (filter_str
                      .replace('(', '%28')
                      .replace(')', '%29')
                      .replace('>', '%3E')
                      .replace("'", '%27'))
    url = (
        'https://datacenter-web.eastmoney.com/api/data/v1/get'
        f'?reportName={report_name}'
        f'&columns=ALL'
        f'&filter={filter_encoded}'
        f'&pageSize={page_size}'
        f'&sortColumns={sort_columns}'
        f'&sortTypes={sort_types}'
        f'&source=WEB&client=WEB'
    )

    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode('utf-8'))

    if not data.get('success'):
        raise Exception(f"API返回失败 [{report_name}]: {data.get('message', '未知错误')}")

    rows = data.get('result', {}).get('data', [])
    if not rows:
        raise Exception(f"API返回空数据 [{report_name}]")

    return rows


def remap_columns(rows, column_map):
    """将API原始字段名映射为统一标准名"""
    items = []
    for row in rows:
        item = {}
        for old_key, new_key in column_map.items():
            val = row.get(old_key)
            if val is not None:
                # TRADE_DATE字段带" 00:00:00"需要截断
                if old_key == 'TRADE_DATE' and isinstance(val, str) and val.endswith(' 00:00:00'):
                    val = val[:10]
                item[new_key] = val
        # 保留所有原始字段
        for k, v in row.items():
            if k not in column_map:
                item[f'_{k}'] = v
        items.append(item)
    return items


def calc_premium_class(premium_ratio):
    """根据折溢价率分类"""
    if premium_ratio is None:
        return 'unknown'
    ratio = float(premium_ratio)
    if ratio > 0.05:  # 溢价>5%
        return 'high_premium'
    elif ratio > 0:   # 溢价0~5%
        return 'premium'
    elif ratio > -0.05:  # 折价0~5%
        return 'slight_discount'
    elif ratio > -0.1:   # 折价5~10%
        return 'discount'
    else:                # 折价>10%
        return 'deep_discount'


def fetch_block_trade_detail():
    """采集大宗交易明细数据"""
    today = datetime.now()
    # 最近20天
    date_filter = (today - timedelta(days=20)).strftime('%Y-%m-%d')
    filter_str = f"(TRADE_DATE>'{date_filter}')"

    rows = fetch_data('RPT_DATA_BLOCKTRADE', filter_str, page_size=5000)

    items = remap_columns(rows, COLUMN_MAP_DETAIL)

    # 按日期排序
    items.sort(key=lambda x: x.get('TRADE_DATE', ''), reverse=True)

    # 获取最新交易日
    latest_date = items[0]['TRADE_DATE'] if items else ''
    latest_items = [i for i in items if i.get('TRADE_DATE') == latest_date] if latest_date else []

    # 只保留A股/EQA (过滤债券等)
    stock_items = [i for i in latest_items if i.get('SECURITY_TYPE') == 'EQA']
    # 如果没有EQA过滤条件，用全部
    stock_items = stock_items or latest_items

    # 按折溢价率排序 (PREMIUM_RATIO: 正=溢价, 负=折价)
    # 折价率最大(最负)的在前
    sorted_by_premium = sorted(stock_items, key=lambda x: float(x.get('PREMIUM_RATIO', 0) or 0))

    # TOP10 折价最大 (折价买入最划算)
    top_discount = sorted_by_premium[:10]
    # TOP10 溢价最大
    top_premium = sorted_by_premium[-10:][::-1]

    # 分类统计
    classification = {'high_premium': 0, 'premium': 0, 'slight_discount': 0,
                      'discount': 0, 'deep_discount': 0, 'unknown': 0}
    for item in stock_items:
        cls = calc_premium_class(item.get('PREMIUM_RATIO'))
        classification[cls] = classification.get(cls, 0) + 1

    # 按金额排序 TOP10
    top_by_amount = sorted(stock_items, key=lambda x: float(x.get('AMOUNT', 0) or 0), reverse=True)[:10]

    # 买方/卖方活跃度统计
    buyer_stats = {}
    seller_stats = {}
    for item in stock_items:
        buyer = item.get('BUYER', '未知')
        seller = item.get('SELLER', '未知')
        amt = float(item.get('AMOUNT', 0) or 0)
        buyer_stats[buyer] = buyer_stats.get(buyer, 0) + amt
        seller_stats[seller] = seller_stats.get(seller, 0) + amt

    top_buyers = sorted(buyer_stats.items(), key=lambda x: -x[1])[:10]
    top_sellers = sorted(seller_stats.items(), key=lambda x: -x[1])[:10]

    result = {
        'source': '东方财富datacenter',
        'report_name': 'RPT_DATA_BLOCKTRADE',
        'fetch_time': datetime.now().isoformat(),
        'trade_date': latest_date,
        'total_count': len(stock_items),
        'total_amount': sum(float(i.get('AMOUNT', 0) or 0) for i in stock_items),
        'classification': classification,
        'top_discount': top_discount,
        'top_premium': top_premium,
        'top_by_amount': top_by_amount,
        'top_buyers': [{'name': k, 'amount': v} for k, v in top_buyers],
        'top_sellers': [{'name': k, 'amount': v} for k, v in top_sellers],
        'status': 'ok',
    }
    return result


def fetch_block_trade_stats():
    """采集大宗交易个股统计(含D1/D5/D10/D20涨跌幅)"""
    today = datetime.now()
    date_filter = (today - timedelta(days=20)).strftime('%Y-%m-%d')
    filter_str = f"(TRADE_DATE>'{date_filter}')"

    rows = fetch_data('RPT_BLOCKTRADE_ACSTA', filter_str, page_size=5000)

    items = remap_columns(rows, COLUMN_MAP_STATS)

    items.sort(key=lambda x: x.get('TRADE_DATE', ''), reverse=True)
    latest_date = items[0]['TRADE_DATE'] if items else ''
    latest_items = [i for i in items if i.get('TRADE_DATE') == latest_date] if latest_date else []

    # 按折溢价率排序
    sorted_by_premium = sorted(latest_items, key=lambda x: float(x.get('AVG_PREMIUM_RATIO', 0) or 0))
    top_discount = sorted_by_premium[:10]
    top_premium = sorted_by_premium[-10:][::-1]

    # 按D1收益排序
    sorted_by_d1 = sorted(
        [i for i in latest_items if i.get('D1_AVG_CHANGE') is not None],
        key=lambda x: float(x['D1_AVG_CHANGE'] or 0), reverse=True
    )

    result = {
        'source': '东方财富datacenter',
        'report_name': 'RPT_BLOCKTRADE_ACSTA',
        'fetch_time': datetime.now().isoformat(),
        'trade_date': latest_date,
        'total_stocks': len(latest_items),
        'top_discount_stats': top_discount,
        'top_premium_stats': top_premium,
        'top_d1_performers': sorted_by_d1[:10],
        'worst_d1_performers': sorted_by_d1[-10:][::-1] if len(sorted_by_d1) >= 10 else [],
        'status': 'ok',
    }
    return result


def main():
    detail_result = None
    stats_result = None
    errors = []

    # 采集1: 大宗交易明细
    try:
        detail_result = fetch_block_trade_detail()
        print(f"[block_trade] 明细API成功: {detail_result.get('total_count', 0)}条记录, "
              f"日期{detail_result.get('trade_date')}")
    except Exception as e:
        errors.append(f"明细API失败: {e}")
        print(f"[block_trade] 明细API异常: {e}")

    # 采集2: 大宗交易统计(含D1/D5/D10)
    try:
        stats_result = fetch_block_trade_stats()
        print(f"[block_trade] 统计API成功: {stats_result.get('total_stocks', 0)}只股票, "
              f"日期{stats_result.get('trade_date')}")
    except Exception as e:
        errors.append(f"统计API失败: {e}")
        print(f"[block_trade] 统计API异常: {e}")

    # 合并结果
    result = {
        'fetch_time': datetime.now().isoformat(),
        'trade_date': (detail_result or stats_result or {}).get('trade_date', ''),
        'status': 'ok' if (detail_result or stats_result) else 'error',
        'errors': errors if errors else None,
        'detail': detail_result,
        'stats': stats_result,
    }

    if not detail_result and not stats_result:
        result['status'] = 'error'
        result['errors'] = errors or ['所有API均失败']

    # 写入输出文件
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    # 打印摘要
    if detail_result:
        disc = detail_result.get('top_discount', [])
        prem = detail_result.get('top_premium', [])
        print(f"\n[block_trade] TOP10 折价最大:")
        for i, item in enumerate(disc[:5]):
            premium = float(item.get('PREMIUM_RATIO', 0) or 0) * 100
            print(f"   {i+1}. {item.get('NAME','')}({item.get('CODE','')}) "
                  f"折价率:{premium:.2f}% 成交价:{item.get('DEAL_PRICE','')} "
                  f"金额:{float(item.get('AMOUNT',0) or 0)/1e4:.0f}万")

        print(f"\n[block_trade] TOP10 溢价最大:")
        for i, item in enumerate(prem[:5]):
            premium = float(item.get('PREMIUM_RATIO', 0) or 0) * 100
            print(f"   {i+1}. {item.get('NAME','')}({item.get('CODE','')}) "
                  f"溢价率:{premium:.2f}% 成交价:{item.get('DEAL_PRICE','')} "
                  f"金额:{float(item.get('AMOUNT',0) or 0)/1e4:.0f}万")

    print(f"\n[block_trade] 写入 {OUTPUT_PATH}")

    # JSON输出到stdout
    result['_script'] = 'block_trade_collector'
    print(json.dumps(result, ensure_ascii=False))

    return 0 if result.get('status') == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
