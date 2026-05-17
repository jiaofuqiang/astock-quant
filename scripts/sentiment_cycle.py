#!/usr/bin/env python3
"""
📊 情绪周期快速判断 — 用SQL直接查，不遍历Python
"""
import os, sqlite3, json, subprocess, re
from datetime import datetime

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")
KLINE_DB = os.path.join(DATA_DIR, "kline_cache.db")
OUTPUT_FILE = os.path.join(BASE, "sentiment_cycle.json")

def get_today_quotes():
    """获取今日腾讯实时行情（只看涨停数）"""
    # 用大盘数据替代
    return None

def sql_query(sql):
    """执行SQL并返回结果"""
    try:
        result = subprocess.run(
            ['sqlite3', KLINE_DB, sql],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except:
        return ""

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 情绪周期判断")
    
    # 用SQLite直接查最新数据
    # 获取最新日期
    latest_date = sql_query("SELECT MAX(date) FROM kline WHERE code='000001'")
    if not latest_date:
        print("❌ 无法获取最新日期")
        return
    
    print(f"   最新日期: {latest_date}")
    
    # 查该日涨停数（主板）
    limit_sql = f"""
        SELECT COUNT(*) FROM kline k1
        JOIN kline k2 ON k1.code = k2.code 
            AND k2.date = (
                SELECT MAX(date2) FROM kline k3 
                WHERE k3.code = k1.code AND k3.date < k1.date
            )
        WHERE k1.date = '{latest_date}'
        AND (k1.code >= '600000' AND k1.code < '700000'
             OR k1.code >= '000000' AND k1.code < '003000')
        AND k1.close >= k2.close * 1.095
    """
    
    limit_count = sql_query(limit_sql)
    print(f"   涨停数: {limit_count}")
    
    # 查前一日涨停
    prev_date = sql_query(f"""
        SELECT MAX(date) FROM kline 
        WHERE code='000001' AND date < '{latest_date}'
    """)
    print(f"   前一日: {prev_date}")
    
    # 查昨日涨停股的今日开盘溢价
    premium_sql = f"""
        SELECT ROUND(AVG((k3.open - k2.close) / k2.close * 100), 2)
        FROM kline k1
        JOIN kline k2 ON k1.code = k2.code AND k2.date = '{prev_date}'
        JOIN kline k3 ON k1.code = k3.code AND k3.date = '{latest_date}'
        WHERE k1.date = '{prev_date}'
        AND k1.close >= (
            SELECT k0.close FROM kline k0 
            WHERE k0.code = k1.code AND k0.date < k1.date 
            ORDER BY k0.date DESC LIMIT 1
        ) * 1.095
        AND (k1.code >= '600000' AND k1.code < '700000'
             OR k1.code >= '000000' AND k1.code < '003000')
    """
    
    premium = sql_query(premium_sql)
    print(f"   昨日涨停溢价: {premium}%")
    
    # 判断情绪周期（用最近的已知数据）
    # 由于SQL复杂，我们基于之前回测中已知的涨停数范围做简化判断
    # 回测中涨停日均29.9次，炸板率~35%
    
    # 用简单规则
    limit_up = 0
    try:
        limit_up = int(limit_count)
    except:
        limit_up = 0
    
    if limit_up > 80:
        state = '高潮🚀'
        score = 80
    elif limit_up > 50:
        state = '发酵🔥'
        score = 60
    elif limit_up > 30:
        state = '震荡🌊'
        score = 45
    elif limit_up > 15:
        state = '启动🌱'
        score = 30
    else:
        state = '冰点❄️'
        score = 15
    
    print(f"\n   情绪评分: {score}")
    print(f"   情绪状态: {state}")
    
    output = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'today': {'date': latest_date, 'limit_up': limit_up, 'premium': premium, 'score': score, 'state': state.strip('🚀🔥🌊🌱❄️')},
    }
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n📁 已保存到 {OUTPUT_FILE}")
    
    # 操作建议
    print(f"\n📋 操作建议:")
    if '高潮' in state or '发酵' in state:
        print("   ✅ 做多为主，游资v4信号可积极参与")
    elif '震荡' in state or '启动' in state:
        print("   ⚠️ 轻仓参与，只做联动强度高的信号")
    else:
        print("   ❌ 空仓观望或极轻仓试错")

if __name__ == '__main__':
    main()
