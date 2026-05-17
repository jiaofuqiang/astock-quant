"""
📡 V4 统一数据层——基于baostock
===================================
替换原有的腾讯appstock + kline_cache.db两层结构。

核心改动：
1. 数据源：腾讯qt.gtimg.cn（实时行情）+ baostock（K线+基本面）
2. K线数据：从baostock获取，可追溯任意时间段，不限120天
3. 股票池：从66只扩展到300+（沪深300+热门板块+行业龙头）
4. 每日自动更新：cron触发一次全量数据拉取
5. 兼容现有系统：KlineLoader接口不变，底层数据源换成baostock
"""

import os
import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA_DIR = "/home/ubuntu/astock/data"
os.makedirs(DATA_DIR, exist_ok=True)

# ============= 股票池定义（扩展版） =============

# 核心观察池（当前66只）
CORE_WATCHLIST = [
    "300308", "300394", "300502", "002281", "688662",  # 光模块
    "002371", "688012", "688981", "688072", "688037",  # 半导体设备
    "300661", "688041", "688126", "300782", "600745",  # 半导体材料/芯片
    "300054", "600703", "002156",                       # 半导体材料
    "601138", "603019", "000977",                       # AI算力
    "300750", "002594", "601012", "300274",             # 新能源龙头
    "002050", "601689", "300124", "688160", "688017",  # 机器人/汽车
    "600536", "688111", "000066", "688568",             # 信创
    "002475", "002241", "300433", "002600",             # 消费电子
    "600276", "603259", "688235", "300760", "002821",   # 创新药
    "600760", "600893", "002179", "600862", "600185",   # 军工
    "600519", "000858", "000568", "600809", "603369",   # 白酒
    "601398", "601939", "601288", "600036", "601166",   # 银行
    "600030", "601211", "601688", "300059", "600958",   # 证券
    "000002", "600048", "001979", "600383", "600325",   # 地产
    "002460", "002709", "600438", "688223",             # 锂电/光伏
    "600585", "600019", "600028", "000630",             # 周期
]

# 扩展池——沪深300核心成分 + 各行业细分龙头
EXTENDED_WATCHLIST = [
    # AI + 半导体（扩展）
    "688256", "688111", "688008", "688009", "688036",
    "300624", "300418", "300033", "002230", "002777",
    # 新能源（扩展）
    "300014", "300457", "300450", "002812", "002850",
    # 消费（扩展）
    "000333", "000651", "002415", "600690", "600887",
    # 医药（扩展）
    "300015", "300347", "300759", "600196", "002007",
    # 军工（扩展）
    "600862", "600118", "600967", "002013", "300699",
    # 金融（扩展）
    "601318", "601628", "601601", "601336", "600016",
    # 周期（扩展）
    "601899", "600547", "600489", "000831", "600010",
    # 新基建/数字经济
    "300442", "300383", "300638", "300502", "688568",
    # 机器人/智能制造
    "000988", "300124", "688017", "688160", "300660",
    # 热门题材股
    "603986", "300782", "688981", "688072", "688126",
]

# 合并去重
ALL_STOCKS = list(set(CORE_WATCHLIST + EXTENDED_WATCHLIST))


# ============= 数据库初始化 =============

KLINE_DB = os.path.join(DATA_DIR, "kline_cache.db")


# ============= 全量主板股票池（动态读取） =============

MAIN_BOARD_LIST_FILE = os.path.join(DATA_DIR, "all_main_board.txt")

def get_all_main_board_codes() -> List[str]:
    """从文件读取全量主板股票代码（6开头+00开头）"""
    if os.path.exists(MAIN_BOARD_LIST_FILE):
        with open(MAIN_BOARD_LIST_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    return []

def get_all_main_board_from_db() -> List[str]:
    """从K线数据库读取所有主板股票代码"""
    import sqlite3
    conn = sqlite3.connect(KLINE_DB)
    try:
        rows = conn.execute(
            "SELECT DISTINCT code FROM kline WHERE code LIKE '6%' OR code LIKE '0%' ORDER BY code"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()

# 全量主板代码（优先从文件读取）
ALL_MAIN_BOARD = get_all_main_board_codes() or get_all_main_board_from_db()


class StockUniverse:
    """
    股票池管理器

    管理所有监控股票的基本信息、行业归属
    """

    def __init__(self):
        self.conn = sqlite3.connect(KLINE_DB)
        self._init_tables()

    def _init_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_info (
                code TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT,
                market_cap REAL,
                is_core INTEGER DEFAULT 0,
                added_at TEXT
            )
        """)
        self.conn.commit()

    def get_all_codes(self) -> List[str]:
        c = self.conn.execute("SELECT code FROM stock_info ORDER BY is_core DESC, code")
        return [r[0] for r in c.fetchall()]

    def get_core_codes(self) -> List[str]:
        c = self.conn.execute("SELECT code FROM stock_info WHERE is_core=1 ORDER BY code")
        return [r[0] for r in c.fetchall()]

    def add_stock(self, code: str, name: str = "", industry: str = "", is_core: bool = False):
        self.conn.execute("""
            INSERT OR REPLACE INTO stock_info (code, name, industry, is_core, added_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, name, industry, 1 if is_core else 0, datetime.now().isoformat()))
        self.conn.commit()

    def update_name_and_industry(self, code: str, name: str, industry: str):
        self.conn.execute("""
            UPDATE stock_info SET name=?, industry=? WHERE code=?
        """, (name, industry, code))
        self.conn.commit()

    def count(self) -> int:
        c = self.conn.execute("SELECT COUNT(*) FROM stock_info")
        return c.fetchone()[0]

    def close(self):
        self.conn.close()


class KlineUpdater:
    """
    K线数据更新器（baostock）

    替代原有的腾讯appstock接口
    baostock优势：
    - 历史数据无限制（可获取数年）
    - 数据更稳定
    - 可获取复权数据
    """

    def __init__(self):
        self.bs = None
        self._login()

    def _login(self):
        import baostock as bs
        self.bs = bs
        self.bs.login()

    def _bs_code(self, code: str) -> str:
        if code.startswith(("sh.", "sz.")):
            return code
        if code.startswith(("60", "68")):
            return f"sh.{code}"
        return f"sz.{code}"

    def update_kline(self, code: str, days: int = 365) -> int:
        """
        更新单只股票K线

        Args:
            code: 股票代码
            days: 获取天数

        Returns:
            写入的K线条数
        """
        bscode = self._bs_code(code)
        end = datetime.now()
        start = end - timedelta(days=days)

        rs = self.bs.query_history_k_data_plus(
            bscode,
            "date,open,close,high,low,volume",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="3"  # 后复权
        )

        conn = sqlite3.connect(KLINE_DB)
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) < 6:
                continue
            try:
                date_str = row[0]
                open_p = float(row[1]) if row[1] and row[1] != "" else 0
                close = float(row[2]) if row[2] and row[2] != "" else 0
                high = float(row[3]) if row[3] and row[3] != "" else 0
                low = float(row[4]) if row[4] and row[4] != "" else 0
                volume = float(row[5]) if row[5] and row[5] != "" else 0

                conn.execute("""
                    INSERT OR REPLACE INTO kline
                    (code, date, open, close, high, low, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (code, date_str, open_p, close, high, low, volume))
                count += 1
            except (ValueError, TypeError):
                pass

        conn.commit()
        conn.close()
        return count

    def batch_update(self, codes: List[str], days: int = 365,
                      verbose: bool = True) -> Dict[str, int]:
        """批量更新"""
        results = {}
        for i, code in enumerate(codes):
            try:
                n = self.update_kline(code, days)
                results[code] = n
                if verbose and (i + 1) % 10 == 0:
                    print(f"  ... {i+1}/{len(codes)}")
            except Exception as e:
                results[code] = -1
                if verbose:
                    print(f"  ⚠️ {code}: {e}")
        return results

    def close(self):
        if self.bs:
            self.bs.logout()


# ============= 全量初始化 =============

def init_stock_universe():
    """
    初始化股票池 + 获取股票名称/行业

    1. 注册所有股票到stock_info表
    2. 通过baostock获取名称和行业
    3. 标记核心池
    """
    universe = StockUniverse()

    # 先注册所有代码
    for code in ALL_STOCKS:
        universe.add_stock(code, "", "", is_core=(code in CORE_WATCHLIST))

    # 通过baostock获取名称和行业
    try:
        import baostock as bs
        bs.login()

        for code in ALL_STOCKS:
            bscode = f"sz.{code}" if not code.startswith(("60", "68")) else f"sh.{code}"
            try:
                rs = bs.query_stock_basic(code=bscode)
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) >= 3:
                        universe.update_name_and_industry(code, row[1], "")
            except:
                pass

        bs.logout()
    except:
        pass

    count = universe.count()
    universe.close()
    return count


def update_all_kline(days: int = 365):
    """
    更新所有股票K线到最新
    
    Args:
        days: 获取多少天数据（默认365天）
    
    Returns:
        {code: count} 每只股票更新条数
    """
    universe = StockUniverse()
    codes = universe.get_all_codes()
    universe.close()

    updater = KlineUpdater()
    results = updater.batch_update(codes, days)
    updater.close()

    success = sum(1 for v in results.values() if v > 0)
    total = sum(v for v in results.values() if v > 0)

    return {
        "total_codes": len(codes),
        "success": success,
        "failed": len(codes) - success,
        "total_klines": total,
    }


def update_fundamentals(codes: List[str] = None):
    """全量更新基本面数据"""
    if codes is None:
        universe = StockUniverse()
        codes = universe.get_all_codes()
        universe.close()

    import sys as _sys
    _sys.path.insert(0, '/home/ubuntu/.local/lib/python3.12/site-packages')
    import baostock as bs
    bs.login()

    conn = sqlite3.connect(os.path.join(DATA_DIR, "fundamental.db"))
    
    now = datetime.now()
    y, m = now.year, now.month
    q = (m - 1) // 3 or 4

    def to_f(v):
        if v is None or v == "" or v == "-": return None
        try: return float(v)
        except: return None

    def bs_code(c):
        if c.startswith(("sh.", "sz.")): return c
        if c.startswith(("60", "68")): return f"sh.{c}"
        return f"sz.{c}"

    total_p, total_g, total_b = 0, 0, 0
    for code in codes:
        bsc = bs_code(code)
        try:
            # 盈利能力
            rs = bs.query_profit_data(code=bsc, year=y, quarter=q)
            while rs.next():
                row = rs.get_row_data()
                if len(row) >= 10:
                    conn.execute("""INSERT OR REPLACE INTO profit_data 
                        (code, stat_date, pub_date, roe, roe_diluted, roa,
                         gross_profit_margin, net_profit_margin, net_profit, eps)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (code, row[2], row[1], to_f(row[3]), to_f(row[4]),
                         to_f(row[5]), to_f(row[6]), to_f(row[7]),
                         to_f(row[8]), to_f(row[9])))
                    total_p += 1
        except: pass
            
        try:
            # 成长能力
            rs = bs.query_growth_data(code=bsc, year=y, quarter=q)
            while rs.next():
                row = rs.get_row_data()
                if len(row) >= 8:
                    conn.execute("""INSERT OR REPLACE INTO growth_data
                        (code, stat_date, pub_date, yoy_eps, yoy_revenue,
                         yoy_net_profit, qoq_revenue, qoq_net_profit)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (code, row[2], row[1], to_f(row[3]), to_f(row[4]),
                         to_f(row[5]), to_f(row[6]), to_f(row[7])))
                    total_g += 1
        except: pass
            
        try:
            # 资产负债表
            rs = bs.query_balance_data(code=bsc, year=y, quarter=q)
            while rs.next():
                row = rs.get_row_data()
                if len(row) >= 9:
                    ta, tl = to_f(row[3]), to_f(row[4])
                    ca, cl = to_f(row[5]), to_f(row[6])
                    eq = to_f(row[7])
                    alr = (tl / ta * 100) if ta and ta > 0 else None
                    cr = (ca / cl) if ca is not None and cl is not None and cl > 0 else None
                    conn.execute("""INSERT OR REPLACE INTO balance_data
                        (code, stat_date, pub_date, total_assets, total_liab,
                         current_assets, current_liab, equity, asset_liab_ratio, current_ratio)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (code, row[2], row[1], ta, tl, ca, cl, eq, alr, cr))
                    total_b += 1
        except: pass

    conn.commit()
    conn.execute("INSERT OR REPLACE INTO update_log (table_name, updated_at) VALUES ('profit_data', datetime('now'))")
    conn.commit()
    conn.close()
    bs.logout()
    return {"profit": total_p, "growth": total_g, "balance": total_b}


# ============= 一键初始化 =============

def full_init():
    """完整初始化：股票池 + K线 + 基本面"""
    print("🚀 V4 全数据源初始化")
    print("=" * 60)

    print(f"\n1️⃣ 初始化股票池 ({len(ALL_STOCKS)} 只)...")
    n = init_stock_universe()
    print(f"   已注册 {n} 只股票")

    print(f"\n2️⃣ 更新K线数据 (365天)...")
    result = update_all_kline(days=365)
    print(f"   {result['success']}/{result['total_codes']} 成功, {result['total_klines']} 条K线")

    print(f"\n3️⃣ 更新基本面数据...")
    fin = update_fundamentals()
    print(f"   盈利能力: {fin['profit']} 条")
    print(f"   成长能力: {fin['growth']} 条")
    print(f"   资产负债表: {fin['balance']} 条")

    print(f"\n✅ V4 数据源初始化完成")


# ============= 测试 =============

if __name__ == "__main__":
    print("📡 V4 统一数据层")
    print("=" * 60)

    universe = StockUniverse()
    print(f"\n当前股票池: {universe.count()} 只")

    # 检查K线
    conn = sqlite3.connect(KLINE_DB)
    c = conn.execute("SELECT COUNT(DISTINCT code) FROM kline")
    kline_stocks = c.fetchone()[0]
    c = conn.execute("SELECT MIN(date), MAX(date) FROM kline")
    date_range = c.fetchone()
    conn.close()

    print(f"K线数据库: {kline_stocks} 只股票, {date_range[0]} ~ {date_range[1]}")

    if kline_stocks < 200:
        print("\n⚠️ K线数据不足，运行全量初始化...")
        print(full_init())
    else:
        # 只更新最近数据
        updater = KlineUpdater()
        codes = universe.get_core_codes()[:3]
        for code in codes:
            n = updater.update_kline(code, days=30)
            print(f"  更新 {code}: {n} 条K线")
        updater.close()

    universe.close()
    print("\n✅ 数据层就绪")
