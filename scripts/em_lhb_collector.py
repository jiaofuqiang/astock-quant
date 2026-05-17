#!/usr/bin/env python3
"""
📊 东方财富龙虎榜采集器 v1.0

采集来源：data.eastmoney.com/stock/lhb.html
数据区块：
  1. 个股龙虎榜统计         — 每只股上榜次数+净买额（近1/3/6/12月）
  2. 营业部回报排行         — 每个营业部 T+1~T+10 胜率 ⭐ 核心价值
  3. 证券营业部上榜统计     — 营业部总成交金额排名
  4. 机构买卖情况           — 机构参与个股的买卖情况
  5. 机构席位买卖追踪       — 机构买入/卖出次数追踪
  6. 每日活跃营业部         — 当日Top营业部操作个股明细

采集方式：通过Playwright浏览器渲染后提取
输出：data/em_lhb_cache.db

用法：
  python3 scripts/em_lhb_collector.py              # 采集今天的数据
  python3 scripts/em_lhb_collector.py --date=2026-05-08  # 指定日期
  python3 scripts/em_lhb_collector.py --install-cron      # 安装cron (每天收盘后16:30)
"""

import os, sys, re, json, sqlite3, time
from datetime import datetime, timedelta
from urllib.parse import quote
import urllib.request

BASE = os.path.expanduser("~/astock")
DB_PATH = os.path.join(BASE, "data", "em_lhb_cache.db")
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# ══════════════════════════════════════════
# 数据库
# ══════════════════════════════════════════

def init_db():
    """初始化数据库，创建6张表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 表1: 个股龙虎榜统计 (近一月/三月/六月/一年)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stock_lhb_stats (
            date TEXT, period TEXT,  -- period: 1m/3m/6m/1y
            code TEXT, name TEXT,
           上榜次数 INTEGER,
           龙虎榜净买额_万 REAL,
           龙虎榜买入额_万 REAL,
           龙虎榜卖出额_万 REAL,
           龙虎榜总成交额_万 REAL,
           PRIMARY KEY (date, period, code)
        )
    """)
    
    # 表2: 营业部回报排行 (按时间段) ⭐ 核心
    c.execute("""
        CREATE TABLE IF NOT EXISTS dept_return_rank (
            date TEXT, period TEXT,
            rank_num INTEGER,
            dept_name TEXT,
            d1_count INTEGER, d1_avg_pct REAL, d1_winrate REAL,
            d2_count INTEGER, d2_avg_pct REAL, d2_winrate REAL,
            d3_count INTEGER, d3_avg_pct REAL, d3_winrate REAL,
            d5_count INTEGER, d5_avg_pct REAL, d5_winrate REAL,
            d10_count INTEGER, d10_avg_pct REAL, d10_winrate REAL,
            PRIMARY KEY (date, period, rank_num)
        )
    """)
    
    # 表3: 证券营业部上榜统计
    c.execute("""
        CREATE TABLE IF NOT EXISTS dept_lhb_rank (
            date TEXT, period TEXT,
            rank_num INTEGER,
            dept_name TEXT,
            成交金额_万 REAL,
            上榜次数 INTEGER,
            买入额_万 REAL,
            买入次数 INTEGER,
            卖出额_万 REAL,
            卖出次数 INTEGER,
            PRIMARY KEY (date, period, rank_num)
        )
    """)
    
    # 表4: 机构买卖情况
    c.execute("""
        CREATE TABLE IF NOT EXISTS inst_buy_sell (
            date TEXT,
            code TEXT, name TEXT,
            买方机构数 INTEGER,
            卖方机构数 INTEGER,
            机构买入总额_万 REAL,
            机构卖出总额_万 REAL,
            机构买入净额_万 REAL,
            上榜原因 TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    
    # 表5: 机构席位买卖追踪
    c.execute("""
        CREATE TABLE IF NOT EXISTS inst_seat_track (
            date TEXT,
            code TEXT, name TEXT,
            龙虎榜成交金额_万 REAL,
            上榜次数 INTEGER,
            买入额_万 REAL,
            买入次数 INTEGER,
            卖出额_万 REAL,
            卖出次数 INTEGER,
            机构净买入额_万 REAL,
            PRIMARY KEY (date, code)
        )
    """)
    
    # 表6: 每日活跃营业部
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_active_dept (
            date TEXT,
            rank_num INTEGER,
            dept_name TEXT,
            买入个股数 INTEGER,
            卖出个股数 INTEGER,
            买入总金额_万 REAL,
            卖出总金额_万 REAL,
            买卖净额_万 REAL,
            操作个股 TEXT,
            PRIMARY KEY (date, rank_num)
        )
    """)
    
    conn.commit()
    return conn


# ══════════════════════════════════════════
# 数据解析 (从格式化的文本解析)
# ══════════════════════════════════════════

def parse_dept_return(text):
    """
    解析营业部回报排行数据
    格式示例：
    1\t深股通专用\t839\t0.40%\t48.39%\t816\t0.03%\t45.22%...
    """
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_dept_rank(text):
    """
    解析证券营业部上榜统计
    格式示例：
    1\t深股通专用\t30920201.51\t991\t16526483.60\t978\t14393717.91\t988
    """
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_stock_stats(text):
    """
    解析个股龙虎榜统计
    格式示例：
    1\t920088\t科力股份\t详情 数据 股吧\t38\t-28937.89\t265110.29\t294048.18\t559158.47
    """
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_inst_buy_sell(text):
    """解析机构买卖情况"""
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_inst_track(text):
    """解析机构席位买卖追踪"""
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_active_dept(text):
    """解析每日活跃营业部"""
    rows = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('序号') or line.startswith('点击'):
            continue
        parts = re.split(r'\t+', line)
        if len(parts) >= 4 and parts[0].isdigit():
            rows.append(parts)
    return rows


def parse_pct(s):
    """解析百分比字符串 '48.39%' -> 0.4839"""
    if not s or s == '-':
        return None
    s = s.replace('%', '').strip()
    try:
        return float(s) / 100.0
    except:
        return None


def parse_wan(s):
    """解析金额 '16526483.60' -> 16526483.60"""
    if not s or s == '-':
        return None
    s = s.replace(',', '').strip()
    try:
        return float(s)
    except:
        return None


# ══════════════════════════════════════════
# 存入数据库
# ══════════════════════════════════════════

def save_all(conn, date_str, sections):
    """将所有解析后的数据存入数据库"""
    c = conn.cursor()
    
    # 1. 营业部回报排行 (按近一月)
    if 'dept_return' in sections and sections['dept_return']:
        rows = parse_dept_return(sections['dept_return'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO dept_return_rank
                    (date, period, rank_num, dept_name,
                     d1_count, d1_avg_pct, d1_winrate,
                     d2_count, d2_avg_pct, d2_winrate,
                     d3_count, d3_avg_pct, d3_winrate,
                     d5_count, d5_avg_pct, d5_winrate,
                     d10_count, d10_avg_pct, d10_winrate)
                    VALUES (?, '1m', ?, ?,
                            ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?)
                """, (
                    date_str, int(row[0]), row[1][:100],
                    int(row[2]), parse_pct(row[3]), parse_pct(row[4]),
                    int(row[5]), parse_pct(row[6]), parse_pct(row[7]),
                    int(row[8]), parse_pct(row[9]), parse_pct(row[10]),
                    int(row[11]), parse_pct(row[12]), parse_pct(row[13]),
                    int(row[14]), parse_pct(row[15]), parse_pct(row[16]),
                ))
            except (IndexError, ValueError) as e:
                pass
    
    # 2. 证券营业部上榜统计
    if 'dept_rank' in sections and sections['dept_rank']:
        rows = parse_dept_rank(sections['dept_rank'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO dept_lhb_rank
                    (date, period, rank_num, dept_name,
                     成交金额_万, 上榜次数, 买入额_万, 买入次数, 卖出额_万, 卖出次数)
                    VALUES (?, '1m', ?, ?,
                            ?, ?, ?, ?, ?, ?)
                """, (
                    date_str, int(row[0]), row[1][:100],
                    parse_wan(row[2]), int(row[3]),
                    parse_wan(row[4]), int(row[5]),
                    parse_wan(row[6]), int(row[7]),
                ))
            except (IndexError, ValueError) as e:
                pass
    
    # 3. 个股龙虎榜统计
    if 'stock_stats' in sections and sections['stock_stats']:
        rows = parse_stock_stats(sections['stock_stats'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO stock_lhb_stats
                    (date, period, code, name,
                     上榜次数, 龙虎榜净买额_万, 龙虎榜买入额_万,
                     龙虎榜卖出额_万, 龙虎榜总成交额_万)
                    VALUES (?, '1m', ?, ?,
                            ?, ?, ?, ?, ?)
                """, (
                    date_str, row[1][:6].strip(), row[2][:20],
                    int(row[4]),
                    parse_wan(row[5]), parse_wan(row[6]),
                    parse_wan(row[7]), parse_wan(row[8]),
                ))
            except (IndexError, ValueError) as e:
                pass
    
    # 4. 机构买卖情况
    if 'institution' in sections and sections['institution']:
        rows = parse_inst_buy_sell(sections['institution'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO inst_buy_sell
                    (date, code, name,
                     买方机构数, 卖方机构数,
                     机构买入总额_万, 机构卖出总额_万,
                     机构买入净额_万, 上榜原因)
                    VALUES (?, ?, ?,
                            ?, ?, ?, ?, ?, ?)
                """, (
                    date_str, row[1][:6].strip(), row[2][:20],
                    int(row[4]), int(row[5]),
                    parse_wan(row[6]), parse_wan(row[7]),
                    parse_wan(row[8]), row[9][:200] if len(row) > 9 else '',
                ))
            except (IndexError, ValueError) as e:
                pass
    
    # 5. 机构席位买卖追踪
    if 'institution_track' in sections and sections['institution_track']:
        rows = parse_inst_track(sections['institution_track'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO inst_seat_track
                    (date, code, name,
                     龙虎榜成交金额_万, 上榜次数,
                     买入额_万, 买入次数,
                     卖出额_万, 卖出次数,
                     机构净买入额_万)
                    VALUES (?, ?, ?,
                            ?, ?,
                            ?, ?, ?, ?, ?)
                """, (
                    date_str, row[1][:6].strip(), row[2][:20],
                    parse_wan(row[4]), int(row[5]),
                    parse_wan(row[6]), int(row[7]),
                    parse_wan(row[8]), int(row[9]),
                    parse_wan(row[10]),
                ))
            except (IndexError, ValueError) as e:
                pass
    
    # 6. 每日活跃营业部
    if 'active_dept' in sections and sections['active_dept']:
        rows = parse_active_dept(sections['active_dept'])
        for row in rows:
            try:
                c.execute("""
                    INSERT OR REPLACE INTO daily_active_dept
                    (date, rank_num, dept_name,
                     买入个股数, 卖出个股数,
                     买入总金额_万, 卖出总金额_万,
                     买卖净额_万, 操作个股)
                    VALUES (?, ?, ?,
                            ?, ?,
                            ?, ?, ?, ?)
                """, (
                    date_str, int(row[0]), row[1][:100],
                    int(row[2]) if row[2].isdigit() else 0,
                    int(row[3]) if row[3].isdigit() else 0,
                    parse_wan(row[4]), parse_wan(row[5]),
                    parse_wan(row[6]), row[7][:500] if len(row) > 7 else '',
                ))
            except (IndexError, ValueError) as e:
                pass
    
    conn.commit()


# ══════════════════════════════════════════
# 浏览器采集 (通过playwright)
# ══════════════════════════════════════════

def collect_via_browser(date_str):
    """
    通过Playwright启动浏览器，打开东方财富龙虎榜首页，
    等待渲染完成后提取所有数据区块
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 需要安装Playwright: pip install playwright && playwright install chromium")
        return None
    
    url = "https://data.eastmoney.com/stock/lhb.html"
    sections = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until='networkidle', timeout=30000)
        
        # 等待数据表格渲染
        time.sleep(3)
        
        # 获取页面全文本
        text = page.evaluate("document.body.innerText")
        
        if not text or len(text) < 500:
            print(f"⚠️ 页面内容不足 ({len(text) if text else 0} chars)，可能被反爬")
            browser.close()
            return None
        
        # 提取各区块
        markers = {
            'stock_stats': '个股龙虎榜统计',
            'institution': '机构买卖情况',
            'institution_track': '机构席位买卖追踪',
            'active_dept': '每日活跃营业部',
            'dept_return': '营业部回报排行',
            'dept_rank': '证券营业部上榜统计',
        }
        
        for key, marker in markers.items():
            idx = text.find(marker)
            if idx >= 0:
                # 找下一个区块的起始位置
                next_markers = list(markers.values())
                next_idx = text.find('证券营业部查询', idx + len(marker))
                if next_idx < 0:
                    next_idx = text.find('龙虎榜单解读', idx + len(marker))
                if next_idx < 0:
                    next_idx = idx + 2000
                sections[key] = text[idx:next_idx]
        
        browser.close()
    
    if not sections:
        print("❌ 未提取到任何数据区块")
        return None
    
    print(f"✅ 成功提取 {len(sections)} 个数据区块")
    for k in sections:
        lines = sections[k].strip().split('\n')
        print(f"   {k}: {len(lines)} 行")
    
    return sections


# ══════════════════════════════════════════
# 命令行
# ══════════════════════════════════════════

def install_cron():
    """安装收盘后cron任务"""
    script_path = os.path.abspath(__file__)
    cron_line = f"30 16 * * 1-5 cd {BASE} && python3 {script_path} >> {BASE}/logs/em_lhb_collector.log 2>&1"
    
    # 检查是否已安装
    import subprocess
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    existing = result.stdout
    
    if 'em_lhb_collector' in existing:
        print("⚠️ cron任务已存在，跳过")
        return
    
    new_cron = existing.strip() + '\n' + cron_line + '\n'
    subprocess.run(['crontab'], input=new_cron, text=True)
    print(f"✅ cron已添加: {cron_line}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='东方财富龙虎榜数据采集器')
    parser.add_argument('--date', help='采集指定日期 (默认今天)')
    parser.add_argument('--install-cron', action='store_true', help='安装收盘后cron任务')
    parser.add_argument('--test-parse', help='测试解析一段文本 (传文件路径)')
    args = parser.parse_args()
    
    if args.install_cron:
        install_cron()
        return
    
    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    
    print(f"📊 东方财富龙虎榜采集 [{date_str}]")
    print("=" * 50)
    
    # 采集数据
    print("⏳ 启动浏览器采集...")
    sections = collect_via_browser(date_str)
    
    if not sections:
        print("❌ 采集失败")
        sys.exit(1)
    
    # 存入数据库
    conn = init_db()
    save_all(conn, date_str, sections)
    
    # 统计
    c = conn.cursor()
    counts = {}
    for table in ['dept_return_rank', 'dept_lhb_rank', 'stock_lhb_stats', 
                  'inst_buy_sell', 'inst_seat_track', 'daily_active_dept']:
        c.execute(f"SELECT COUNT(*) FROM {table} WHERE date=?", (date_str,))
        counts[table] = c.fetchone()[0]
    
    conn.close()
    
    print(f"\n✅ 采集完成! 存入数据:")
    for table, count in counts.items():
        print(f"   {table}: {count} 条")
    print(f"\n📁 数据库: {DB_PATH}")
    
    # 打印核心数据预览
    print(f"\n📈 营业部回报排行 Top 5 (近一月):")
    conn = init_db()
    c = conn.cursor()
    c.execute("""
        SELECT dept_name, d1_count, d1_winrate, d2_winrate, d3_winrate
        FROM dept_return_rank
        WHERE date=? AND period='1m'
        ORDER BY rank_num
        LIMIT 5
    """, (date_str,))
    for row in c.fetchall():
        name, cnt, wr1, wr2, wr3 = row
        print(f"   {name[:20]:20s}  {cnt:4d}次  T+1:{wr1:.1%}  T+2:{wr2:.1%}  T+3:{wr3:.1%}")
    conn.close()


if __name__ == '__main__':
    main()
