#!/usr/bin/env python3
"""
板块每日指数构建器 v1.0
用途：基于 kline_cache.db 日K线 + chain_engine.db 板块映射
      一次性构建所有板块的历史每日指数

输出：data/sector_indexes.db
  表1：sector_daily_index — 板块每日指数
  表2：sector_stock_daily — 板块内个股每日排名
"""

import sqlite3
import subprocess
import sys
import os
import time
from datetime import datetime
from collections import defaultdict

A_STOCK = os.path.expanduser("~/astock")
KLINE_DB = os.path.join(A_STOCK, "data/kline_cache.db")
CHAIN_DB = os.path.join(A_STOCK, "data/chain_engine.db")
SECTOR_DB = os.path.join(A_STOCK, "data/sector_indexes.db")

def sql(db, query):
    """用 sqlite3 命令行安全执行查询"""
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', db, query],
                       capture_output=True, timeout=120, text=True)
    if r.returncode != 0:
        print(f"SQL ERROR: {r.stderr[:200]}", file=sys.stderr)
        return []
    return [l for l in r.stdout.strip().split('\n') if l.strip()]

def sql_commit(db, query):
    """执行写入操作"""
    r = subprocess.run(['sqlite3', db, query],
                       capture_output=True, timeout=120, text=True)
    if r.returncode != 0:
        print(f"COMMIT ERROR: {r.stderr[:200]}", file=sys.stderr)
        return False
    return True

def get_sectors():
    """获取所有level2板块及其成分股"""
    rows = sql(CHAIN_DB, """
        SELECT level2, group_concat(code) 
        FROM stock_chain_v2 
        WHERE level2 IS NOT NULL AND level2 != '' AND level2 != '其他'
        GROUP BY level2
        ORDER BY level2
    """)
    sectors = {}
    for row in rows:
        parts = row.split('|', 1)
        if len(parts) == 2:
            name, codes = parts
            sectors[name] = codes.split(',')
    return sectors

def get_stock_info():
    """获取股票名称"""
    rows = sql(KLINE_DB, "SELECT DISTINCT code FROM kline ORDER BY code")
    return [r.strip() for r in rows if r.strip()]

def get_names():
    """从stock_info表获取股票名"""
    rows = sql(KLINE_DB, """
        SELECT DISTINCT s.code, COALESCE(i.name, '') 
        FROM (SELECT DISTINCT code FROM kline) s
        LEFT JOIN stock_info i ON s.code = i.code
    """)
    names = {}
    for row in rows:
        parts = row.split('|')
        if len(parts) >= 2:
            names[parts[0].strip()] = parts[1].strip()
    return names

def get_dates():
    """获取所有交易日"""
    rows = sql(KLINE_DB, "SELECT DISTINCT date FROM kline ORDER BY date")
    return [r.strip() for r in rows if r.strip()]

def build_indexes(sectors, debug_mode=False):
    """构建所有板块每日指数"""
    print(f"开始构建板块指数: {len(sectors)}个板块")
    print(f"板块列表: {', '.join(sectors.keys())}")
    
    all_dates = get_dates()
    print(f"总交易日: {len(all_dates)}")
    
    stock_names = get_names()
    print(f"有名称的股票: {len(stock_names)}")
    
    # 先查已有数据看进度
    existing_dates = set()
    existing_rows = sql(SECTOR_DB, "SELECT DISTINCT sector_name, date FROM sector_daily_index")
    for row in existing_rows:
        parts = row.split('|')
        if len(parts) >= 2:
            existing_dates.add(f"{parts[0]}|{parts[1]}")
    
    print(f"已有板块日数据: {len(existing_dates)}条")
    
    total_expected = len(sectors) * len(all_dates)
    done = 0
    if debug_mode:
        done = len(existing_dates)
        print(f"已完成: {done}/{total_expected} ({done*100//max(total_expected,1)}%)")
    
    total_batch = 0
    
    for sname, codes in sectors.items():
        s_codes_list = [c for c in codes if c in stock_names]
        if not s_codes_list:
            print(f"  ⚠ {sname}: 无有效股票")
            continue
        
        # 批量获取该板块所有股票的全部K线
        # 拆小批避免命令行参数过长
        code_batches = [s_codes_list[i:i+30] for i in range(0, len(s_codes_list), 30)]
        
        kline_data = defaultdict(dict)  # {code: {date: {open,close,high,low,volume}}}
        
        for batch in code_batches:
            codes_str = ','.join(f"'{c}'" for c in batch)
            rows = sql(KLINE_DB, f"""
                SELECT code, date, close, volume 
                FROM kline 
                WHERE code IN ({codes_str})
            """)
            for row in rows:
                parts = row.split('|')
                if len(parts) >= 4:
                    code, date = parts[0].strip(), parts[1].strip()
                    try:
                        kline_data[code][date] = {
                            'close': float(parts[2]),
                            'volume': float(parts[3])
                        }
                    except ValueError:
                        pass
        
        if not kline_data:
            print(f"  ⚠ {sname}: 无K线数据")
            continue
        
        # 逐日计算板块指数
        daily_rows = []  # 攒着批量写入
        stock_daily_rows = []
        
        for i, date in enumerate(all_dates):
            if not debug_mode:
                # 检查已有
                key = f"{sname}|{date}"
                if key in existing_dates:
                    continue
            
            # 获取该日所有板块股票的收盘数据
            day_changes = []
            day_stocks = []
            
            for code in s_codes_list:
                if code not in kline_data or date not in kline_data[code]:
                    continue
                rec = kline_data[code][date]
                day_stocks.append((code, rec['close'], rec['volume']))
            
            if len(day_stocks) < 3:
                continue  # 板块不足3只有效数据
            
            # 计算涨跌幅（需要对比前一日）
            prev_date = all_dates[i-1] if i > 0 else None
            if not prev_date:
                continue
            
            changes = []
            for code, close, vol in day_stocks:
                if code not in kline_data or prev_date not in kline_data[code]:
                    continue
                prev_close = kline_data[code][prev_date]['close']
                prev_vol = kline_data[code][prev_date]['volume']
                if prev_close <= 0:
                    continue
                change = (close - prev_close) / prev_close * 100
                vol_ratio = (vol / prev_vol) if prev_vol > 0 else 1.0
                
                # 判断涨跌停
                is_limit_up = 1 if change >= 9.5 else 0
                is_limit_down = 1 if change <= -9.5 else 0
                
                changes.append((code, change, vol_ratio, is_limit_up, is_limit_down))
            
            if len(changes) < 3:
                continue
            
            # 计算板块统计
            pcts = [c[1] for c in changes]
            vols = [c[2] for c in changes]
            avg_c = sum(pcts) / len(pcts)
            sorted_pcts = sorted(pcts)
            median_c = sorted_pcts[len(sorted_pcts)//2]
            max_c = sorted_pcts[-1]
            min_c = sorted_pcts[0]
            variance = sum((p - avg_c)**2 for p in pcts) / len(pcts)
            std_c = variance ** 0.5
            up_c = sum(1 for p in pcts if p > 0)
            down_c = sum(1 for p in pcts if p < 0)
            limit_up = sum(c[3] for c in changes)
            limit_down = sum(c[4] for c in changes)
            avg_vr = sum(c[2] for c in changes) / len(changes)
            
            # 板块指数行
            daily_rows.append(
                f"('{sname}','{date}',{len(changes)},{avg_c:.4f},{median_c:.4f},{max_c:.4f},{min_c:.4f},{std_c:.4f},{up_c},{down_c},{limit_up},{limit_down},{avg_vr:.4f})"
            )
            
            # 板块内个股排名
            ranked = sorted(changes, key=lambda x: x[1], reverse=True)
            for rank, (code, change, vr, lu, ld) in enumerate(ranked, 1):
                name = stock_names.get(code, '')
                stock_daily_rows.append(
                    f"('{sname}','{date}','{code}','{name}',{change:.4f},{vr:.4f},{rank},{lu},{ld})"
                )
            
            if debug_mode and (i+1) % 200 == 0:
                dt = datetime.fromtimestamp(time.time())
                print(f"  {sname}: {i+1}/{len(all_dates)}日 ({date})", end='\r')
        
        # 批量写入板块指数
        if daily_rows:
            batch_size = 500
            for j in range(0, len(daily_rows), batch_size):
                batch = daily_rows[j:j+batch_size]
                sql_text = f"INSERT OR IGNORE INTO sector_daily_index VALUES {','.join(batch)};"
                with open('/tmp/sector_batch.sql', 'w') as f:
                    f.write(sql_text)
                sql_commit(SECTOR_DB, f".read /tmp/sector_batch.sql")
            
            # 批量写入个股排名
            for j in range(0, len(stock_daily_rows), batch_size):
                batch = stock_daily_rows[j:j+batch_size]
                sql_text = f"INSERT OR IGNORE INTO sector_stock_daily VALUES {','.join(batch)};"
                with open('/tmp/sector_stock_batch.sql', 'w') as f:
                    f.write(sql_text)
                sql_commit(SECTOR_DB, f".read /tmp/sector_stock_batch.sql")
            
            total_batch += len(daily_rows)
            
            if debug_mode:
                print(f"  {sname}: 写入 {len(daily_rows)} 条板块指数 + {len(stock_daily_rows)} 条个股排名")
            else:
                print(f"  ✅ {sname}: {len(daily_rows)} 条板块指数 ({len(s_codes_list)}只成分股)")
    
    return total_batch

def main():
    print(f"=== 板块每日指数构建器 ===")
    print(f"K线: {KLINE_DB}")
    print(f"板块映射: {CHAIN_DB}")
    print(f"输出: {SECTOR_DB}")
    print()
    
    debug = '--debug' in sys.argv
    
    sectors = get_sectors()
    print(f"共 {len(sectors)} 个板块")
    
    # 过滤出科技相关热门板块（先全量建，重点关注）
    key_sectors = ['机器人', '低空经济', '半导体', '储能', '锂电池', '新能源汽车', 
                   '消费电子', '光模块与光通信', '数据中心', '智能驾驶', 'AI算力',
                   '存储芯片', 'AI芯片', '软件与应用', '军工', '光伏', '风电',
                   '汽车电子', '液冷与散热', '创新药', '生物医药', '医疗器械']
    
    total = build_indexes(sectors, debug)
    
    print(f"\n✅ 完成! 共写入 {total} 条板块指数")
    
    # 统计
    rows = sql(SECTOR_DB, "SELECT COUNT(*) FROM sector_daily_index")
    print(f"sector_daily_index 总条数: {rows[0] if rows else 0}")
    rows = sql(SECTOR_DB, "SELECT COUNT(*) FROM sector_stock_daily")
    print(f"sector_stock_daily 总条数: {rows[0] if rows else 0}")
    
    # 显示各板块数据量
    rows = sql(SECTOR_DB, """
        SELECT sector_name, COUNT(*) as days, 
               ROUND(AVG(stock_count), 0) as avg_stocks
        FROM sector_daily_index 
        GROUP BY sector_name 
        ORDER BY days DESC
    """)
    print("\n各板块数据覆盖:")
    for row in rows:
        parts = row.split('|')
        if len(parts) >= 3:
            print(f"  {parts[0]:12s}: {parts[1]:>5}日, 平均{parts[2]:>4}只")

if __name__ == '__main__':
    main()
