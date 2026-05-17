#!/usr/bin/env python3
"""
环境日数据回填器 — 用kline_cache.db的历史K线数据构建每日市场环境数据
解决market_daily.db只有3天数据的问题，让穿透评分能回看历史环境。
"""
import sqlite3, os, json, time
from datetime import datetime

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
OUTPUT = os.path.join(HOME, "astock/data/env_daily_history.json")
OUTPUT_MD = os.path.join(HOME, "V2board/data/env_daily_history.json")

def main():
    print("="*70)
    print("环境日数据回填器 — 从K线构建每日市场环境")
    print(f"启动: {datetime.now().strftime('%H:%M:%S')}")
    print("="*70)
    
    conn = sqlite3.connect(KLINE_DB)
    
    # 查询所有交易日的主板股票K线
    print("\n📊 查询每日全市场数据...")
    start = time.time()
    rows = conn.execute("""
        SELECT date,
            COUNT(*) as total,
            SUM(CASE WHEN close>open THEN 1 ELSE 0 END) as up,
            SUM(CASE WHEN close<open THEN 1 ELSE 0 END) as down,
            SUM(CASE WHEN close>=open*1.095 THEN 1 ELSE 0 END) as limit_up,
            SUM(CASE WHEN close<=open*0.905 THEN 1 ELSE 0 END) as limit_down,
            ROUND(AVG((close-open)/open*100), 2) as avg_chg,
            ROUND(AVG(volume), 0) as avg_vol,
            SUM(volume) as total_vol
        FROM kline
        WHERE date >= '2024-01-01'
          AND (code LIKE '6%' OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        GROUP BY date
        ORDER BY date
    """).fetchall()
    print(f"  ✅ {len(rows)}个交易日 ({time.time()-start:.1f}s)")
    
    # 计算环境分
    env_daily = {}
    for r in rows:
        date, total, up, down, limit_up, limit_down, avg_chg, avg_vol, total_vol = r
        
        up_ratio = up / total if total > 0 else 0.5
        
        # 环境评分（参考穿透评分大盘层逻辑）
        # 涨跌比分（核心）
        if up_ratio >= 0.65: env_up = 85  # 高潮
        elif up_ratio >= 0.55: env_up = 65  # 发酵
        elif up_ratio >= 0.45: env_up = 45  # 震荡
        elif up_ratio >= 0.35: env_up = 25  # 弱势
        else: env_up = 10  # 冰点
        
        # 涨停强度分
        limit_up_ratio = limit_up / total if total > 0 else 0
        if limit_up_ratio >= 0.03: env_zt = 80
        elif limit_up_ratio >= 0.02: env_zt = 60
        elif limit_up_ratio >= 0.01: env_zt = 40
        elif limit_up > 0: env_zt = 20
        else: env_zt = 5
        
        # 涨停净力（涨停-跌停）
        net = limit_up - limit_down
        if net >= 50: env_net = 85
        elif net >= 25: env_net = 65
        elif net >= 10: env_net = 45
        elif net >= 0: env_net = 25
        else: env_net = 10
        
        # 综合环境分
        env_score = round(env_up * 0.40 + env_zt * 0.30 + env_net * 0.30)
        
        env_daily[date] = {
            "date": date,
            "total_stocks": total,
            "up": up,
            "down": down,
            "up_ratio": round(up_ratio, 3),
            "limit_up": limit_up,
            "limit_down": limit_down,
            "net_limit": net,
            "avg_chg": avg_chg,
            "env_up": env_up,
            "env_zt": env_zt,
            "env_net": env_net,
            "env_score": env_score,
            "env_level": "冰点" if env_score < 25 else ("弱势" if env_score < 40 else ("震荡" if env_score < 55 else ("发酵" if env_score < 70 else "高潮"))),
        }
    
    # 输出到JSON
    output = {
        "meta": {
            "source": "kline_cache.db → 环境回填器",
            "generated_at": datetime.now().isoformat(),
            "total_days": len(rows),
            "date_range": f"{rows[0][0]} ~ {rows[-1][0]}" if rows else "",
        },
        "daily": env_daily,
        "summary": {},
    }
    
    # 汇总统计
    env_levels = {}
    for date, data in env_daily.items():
        level = data["env_level"]
        env_levels[level] = env_levels.get(level, 0) + 1
    output["summary"]["env_distribution"] = env_levels
    
    # 保存
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False)
    
    # 同时写到V2board/data目录
    os.makedirs(os.path.dirname(OUTPUT_MD), exist_ok=True)
    with open(OUTPUT_MD, 'w') as f:
        json.dump(output, f, ensure_ascii=False)
    
    print(f"\n📊 环境分布:")
    for level, cnt in sorted(env_levels.items(), key=lambda x: ['冰点','弱势','震荡','发酵','高潮'].index(x[0]) if x[0] in ['冰点','弱势','震荡','发酵','高潮'] else 99):
        print(f"  {level}: {cnt}天 ({round(cnt/len(rows)*100,1)}%)")
    
    print(f"\n✅ 已保存:")
    print(f"  {OUTPUT}")
    print(f"  {OUTPUT_MD}")
    print(f"  共{len(rows)}天环境数据")
    
    conn.close()

if __name__ == '__main__':
    main()
