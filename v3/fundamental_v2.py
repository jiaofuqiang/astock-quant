"""
🏢 V3.8 完整基本面分析层（基于baostock）
===========================================
使用baostock获取免费的中国A股基本面数据。
之前V3.7用的腾讯接口只能拿到PE/市值，现在有完整的财务数据了。

baostock可提供：
- 盈利能力: ROE、ROA、毛利率、净利率、EPS
- 成长能力: 营收增速、净利润增速（同比/环比）
- 偿债能力: 资产负债率、流动比率、速动比率
- 营运能力: 资产周转率、存货周转率
- 杜邦分析: 驱动因素分解
- 业绩快报/预告

数据获取方式：每日一次，缓存到本地SQLite。
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA_DIR = "/home/ubuntu/astock/data"
DB_PATH = os.path.join(DATA_DIR, "fundamental.db")

# baostock的行业分类
INDUSTRY_MAP = {}

INIT_SQL = """
CREATE TABLE IF NOT EXISTS profit_data (
    code TEXT, stat_date TEXT, pub_date TEXT,
    roe REAL, roe_diluted REAL, roa REAL,
    gross_profit_margin REAL, net_profit_margin REAL,
    net_profit REAL, eps REAL,
    PRIMARY KEY (code, stat_date)
);

CREATE TABLE IF NOT EXISTS growth_data (
    code TEXT, stat_date TEXT, pub_date TEXT,
    yoy_eps REAL, yoy_revenue REAL, yoy_net_profit REAL,
    qoq_revenue REAL, qoq_net_profit REAL,
    PRIMARY KEY (code, stat_date)
);

CREATE TABLE IF NOT EXISTS balance_data (
    code TEXT, stat_date TEXT, pub_date TEXT,
    total_assets REAL, total_liab REAL,
    current_assets REAL, current_liab REAL,
    equity REAL, asset_liab_ratio REAL,
    current_ratio REAL,
    PRIMARY KEY (code, stat_date)
);

CREATE TABLE IF NOT EXISTS cash_flow_data (
    code TEXT, stat_date TEXT, pub_date TEXT,
    operate_cash REAL, invest_cash REAL, finance_cash REAL,
    PRIMARY KEY (code, stat_date)
);

CREATE TABLE IF NOT EXISTS stock_basic (
    code TEXT PRIMARY KEY,
    name TEXT, ipo_date TEXT, out_date TEXT,
    type TEXT, status TEXT, industry TEXT
);

CREATE TABLE IF NOT EXISTS kline_data (
    code TEXT, date TEXT,
    open REAL, close REAL, high REAL, low REAL, volume REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS industry_stocks (
    industry TEXT, code TEXT,
    PRIMARY KEY (industry, code)
);

CREATE TABLE IF NOT EXISTS update_log (
    table_name TEXT PRIMARY KEY,
    updated_at TEXT
);
"""

# PE基准（行业合理PE范围）
SECTOR_PE_RANGES = {
    "通信": (20, 40, 70),
    "电子": (25, 50, 80),
    "计算机": (30, 55, 85),
    "电力设备": (15, 30, 55),
    "机械设备": (15, 30, 50),
    "汽车": (15, 28, 45),
    "医药生物": (25, 45, 75),
    "食品饮料": (20, 35, 55),
    "银行": (4, 6, 10),
    "房地产": (5, 10, 18),
    "非银金融": (12, 20, 35),
    "有色金属": (15, 25, 45),
    "基础化工": (12, 22, 38),
    "国防军工": (35, 55, 90),
    "公用事业": (12, 20, 30),
    "交通运输": (10, 18, 30),
    "建筑装饰": (6, 12, 20),
    "采掘": (8, 15, 25),
    "钢铁": (5, 10, 18),
    "传媒": (20, 35, 60),
    "纺织服装": (10, 20, 35),
    "商业贸易": (10, 18, 30),
    "农林牧渔": (15, 25, 45),
    "综合": (15, 30, 50),
    "default": (15, 30, 60),
}


class FundamentalDB:
    """基本面数据库管理"""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(INIT_SQL)
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(DB_PATH)

    def save_profit(self, code: str, stat_date: str, pub_date: str,
                     roe: float, roe_d: float, roa: float,
                     gpm: float, npm: float, np: float, eps: float):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO profit_data
            (code, stat_date, pub_date, roe, roe_diluted, roa,
             gross_profit_margin, net_profit_margin, net_profit, eps)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (code, stat_date, pub_date, roe, roe_d, roa,
              gpm, npm, np, eps))
        conn.commit()
        conn.close()

    def save_growth(self, code: str, stat_date: str, pub_date: str,
                     yoy_eps: float, yoy_rev: float, yoy_np: float,
                     qoq_rev: float, qoq_np: float):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO growth_data
            (code, stat_date, pub_date, yoy_eps, yoy_revenue,
             yoy_net_profit, qoq_revenue, qoq_net_profit)
            VALUES (?,?,?,?,?,?,?,?)
        """, (code, stat_date, pub_date, yoy_eps, yoy_rev,
              yoy_np, qoq_rev, qoq_np))
        conn.commit()
        conn.close()

    def save_balance(self, code: str, stat_date: str, pub_date: str,
                      tota: float, totl: float, cura: float, curl: float,
                      eq: float, alr: float, cr: float):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO balance_data
            (code, stat_date, pub_date, total_assets, total_liab,
             current_assets, current_liab, equity, asset_liab_ratio, current_ratio)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (code, stat_date, pub_date, tota, totl, cura, curl,
              eq, alr, cr))
        conn.commit()
        conn.close()

    def save_kline(self, code: str, date: str, open_p: float,
                    close: float, high: float, low: float, volume: float):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO kline_data
            (code, date, open, close, high, low, volume)
            VALUES (?,?,?,?,?,?,?)
        """, (code, date, open_p, close, high, low, volume))
        conn.commit()
        conn.close()

    def save_industry_stock(self, industry: str, code: str):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO industry_stocks (industry, code) VALUES (?,?)
        """, (industry, code))
        conn.commit()
        conn.close()

    def set_updated(self, table: str):
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO update_log (table_name, updated_at)
            VALUES (?, ?)
        """, (table, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def needs_update(self, table: str, max_age_hours: int = 24) -> bool:
        conn = self._conn()
        c = conn.execute("SELECT updated_at FROM update_log WHERE table_name=?", (table,))
        row = c.fetchone()
        conn.close()
        if not row:
            return True
        try:
            last = datetime.fromisoformat(row[0])
            return (datetime.now() - last).total_seconds() > max_age_hours * 3600
        except:
            return True

    def get_profit(self, code: str, max_quarters: int = 4) -> List[Dict]:
        conn = self._conn()
        c = conn.execute("""
            SELECT stat_date, roe, roa, gross_profit_margin,
                   net_profit_margin, net_profit, eps
            FROM profit_data WHERE code=?
            ORDER BY stat_date DESC LIMIT ?
        """, (code, max_quarters))
        rows = [dict(zip(["stat_date","roe","roa","gpm","npm","net_profit","eps"], r))
                for r in c.fetchall()]
        conn.close()
        return rows

    def get_growth(self, code: str, max_quarters: int = 4) -> List[Dict]:
        conn = self._conn()
        c = conn.execute("""
            SELECT stat_date, yoy_revenue, yoy_net_profit, qoq_revenue, qoq_net_profit
            FROM growth_data WHERE code=?
            ORDER BY stat_date DESC LIMIT ?
        """, (code, max_quarters))
        rows = [dict(zip(["stat_date","yoy_rev","yoy_np","qoq_rev","qoq_np"], r))
                for r in c.fetchall()]
        conn.close()
        return rows

    def get_balance(self, code: str) -> Optional[Dict]:
        conn = self._conn()
        c = conn.execute("""
            SELECT asset_liab_ratio, current_ratio, equity
            FROM balance_data WHERE code=?
            ORDER BY stat_date DESC LIMIT 1
        """, (code,))
        row = c.fetchone()
        conn.close()
        if row:
            return {"asset_liab_ratio": row[0], "current_ratio": row[1], "equity": row[2]}
        return None

    def get_stock_industry(self, code: str) -> Optional[str]:
        conn = self._conn()
        c = conn.execute("SELECT industry FROM industry_stocks WHERE code=?", (code,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None

    def get_industry_codes(self, industry: str) -> List[str]:
        conn = self._conn()
        c = conn.execute(
            "SELECT code FROM industry_stocks WHERE industry=?", (industry,))
        codes = [r[0] for r in c.fetchall()]
        conn.close()
        return codes

    def get_kline(self, code: str, limit: int = 120) -> List[Dict]:
        conn = self._conn()
        c = conn.execute("""
            SELECT date, open, close, high, low, volume
            FROM kline_data WHERE code=?
            ORDER BY date ASC LIMIT ?
        """, (code, limit))
        rows = [{"date": r[0], "open": r[1], "close": r[2],
                 "high": r[3], "low": r[4], "volume": r[5]}
                for r in c.fetchall()]
        conn.close()
        return rows


class FundamentalFetcher:
    """从baostock获取基本面数据并缓存到SQLite"""

    def __init__(self):
        self.db = FundamentalDB()
        self._init_bs()

    def _init_bs(self):
        import baostock as bs
        self.bs = bs
        self.bs.login()

    def _bs_code(self, code: str) -> str:
        if code.startswith(("sh", "sz")):
            return code
        if code.startswith(("60", "68")):
            return f"sh.{code}"
        return f"sz.{code}"

    def fetch_all_basics(self):
        """获取所有股票的基本信息和行业分类"""
        rs = self.bs.query_stock_basic()
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 6:
                code = row[2]
                name = row[1] if len(row) > 1 else ""
                industry = row[5] if len(row) > 5 else ""
                if industry and industry != "":
                    self.db.save_industry_stock(industry, code)
                count += 1
        self.db.set_updated("stock_basic")
        return count

    def fetch_profit(self, code: str, year: int = None, quarter: int = None):
        """获取盈利能力数据"""
        if year is None:
            year = datetime.now().year
        if quarter is None:
            # 计算最新完整季度
            month = datetime.now().month
            quarter = (month - 1) // 3
            if quarter == 0:
                quarter = 4
                year -= 1

        bscode = self._bs_code(code)
        rs = self.bs.query_profit_data(code=bscode, year=year, quarter=quarter)
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 10:
                self.db.save_profit(
                    code=code,
                    stat_date=row[2],
                    pub_date=row[1],
                    roe=self._f(row[3]),
                    roe_d=self._f(row[4]),
                    roa=self._f(row[5]),
                    gpm=self._f(row[6]),
                    npm=self._f(row[7]),
                    np=self._f(row[8]),
                    eps=self._f(row[9]),
                )
                count += 1
        return count

    def fetch_growth(self, code: str, year: int = None, quarter: int = None):
        """获取成长能力数据"""
        if year is None:
            year = datetime.now().year
            month = datetime.now().month
            quarter = (month - 1) // 3 or 4

        bscode = self._bs_code(code)
        rs = self.bs.query_growth_data(code=bscode, year=year, quarter=quarter)
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 8:
                self.db.save_growth(
                    code=code,
                    stat_date=row[2],
                    pub_date=row[1],
                    yoy_eps=self._f(row[3]),
                    yoy_rev=self._f(row[4]),
                    yoy_np=self._f(row[5]),
                    qoq_rev=self._f(row[6]),
                    qoq_np=self._f(row[7]),
                )
                count += 1
        return count

    def fetch_balance(self, code: str, year: int = None, quarter: int = None):
        """获取资产负债表数据"""
        if year is None:
            year = datetime.now().year
            month = datetime.now().month
            quarter = (month - 1) // 3 or 4

        bscode = self._bs_code(code)
        rs = self.bs.query_balance_data(code=bscode, year=year, quarter=quarter)
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 9:
                ta = self._f(row[3])
                tl = self._f(row[4])
                ca = self._f(row[5])
                cl = self._f(row[6])
                eq = self._f(row[7])
                alr = (tl / ta * 100) if ta and ta > 0 else 0
                cr = (ca / cl) if cl and cl > 0 else 0
                self.db.save_balance(code, row[2], row[1], ta, tl, ca, cl, eq, alr, cr)
                count += 1
        return count

    def fetch_financials(self, code: str):
        """获取一只股票的完整财务数据"""
        import baostock as bs

        bscode = self._bs_code(code)
        now = datetime.now()
        y = now.year
        m = now.month
        q = (m - 1) // 3 or 4

        count = 0
        # 盈利能力
        try:
            c = self.fetch_profit(code, y, q)
            count += c
        except: pass
        # 成长能力
        try:
            c = self.fetch_growth(code, y, q)
            count += c
        except: pass
        # 资产负债
        try:
            c = self.fetch_balance(code, y, q)
            count += c
        except: pass

        return count

    def fetch_kline_from_bs(self, code: str, days: int = 120):
        """从baostock获取K线数据"""
        bscode = self._bs_code(code)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        rs = self.bs.query_history_k_data_plus(
            bscode,
            "date,open,close,high,low,volume",
            start_date=start, end_date=end,
            frequency="d", adjustflag="3"
        )
        count = 0
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 6 and row[1] and row[1] != "":
                try:
                    self.db.save_kline(
                        code, row[0],
                        float(row[1]), float(row[2]),
                        float(row[3]), float(row[4]),
                        float(row[5]) if row[5] else 0
                    )
                    count += 1
                except:
                    pass
        return count

    def _f(self, v):
        """安全转float"""
        if v is None or v == "" or v == "-":
            return None
        try:
            return float(v)
        except:
            return None

    def batch_fetch_all(self, codes: List[str]):
        """批量更新所有股票的数据"""
        # 1. 获取行业分类
        if self.db.needs_update("stock_basic", 168):
            n = self.fetch_all_basics()
            print(f"  行业分类: {n} 只")

        # 2. 逐只获取财务数据
        total_fin = 0
        for code in codes:
            try:
                c = self.fetch_financials(code)
                total_fin += c
            except:
                pass
        print(f"  财务数据: {total_fin} 条")

        return total_fin

    def close(self):
        try:
            self.bs.logout()
        except:
            pass


# ============= 增强版基本面分析器 =============

class EnhancedFundamentalAnalyzer:
    """
    增强版基本面分析器
    
    使用baostock的真实财务数据，替代V3.7中腾讯接口的简化版
    """

    def __init__(self):
        self.db = FundamentalDB()

    def analyze(self, code: str, price_data: Dict = None) -> Dict:
        """完整基本面分析"""
        profit = self.db.get_profit(code, 4)
        growth = self.db.get_growth(code, 4)
        balance = self.db.get_balance(code)
        industry = self.db.get_stock_industry(code)

        # 最新一期数据
        latest_profit = profit[0] if profit else {}
        latest_growth = growth[0] if growth else {}

        # 修正单位：baostock的比例值都是小数（0.175=17.5%），转成百分比数值
        raw_roe = latest_profit.get("roe")
        roe_pct = round(raw_roe * 100, 1) if raw_roe is not None else None
        
        raw_yoy_rev = latest_growth.get("yoy_rev")
        yoy_rev_pct = round(raw_yoy_rev * 100, 1) if raw_yoy_rev is not None else None
        
        raw_yoy_np = latest_growth.get("yoy_np")
        yoy_np_pct = round(raw_yoy_np * 100, 1) if raw_yoy_np is not None else None

        # 从price_data获取实时PE和市值
        pe = price_data.get("pe") if price_data else None
        market_cap = price_data.get("market_cap") if price_data else None

        # === 评分 ===
        scores = {}

        # 1. ROE评分（盈利质量核心）
        if roe_pct is not None and roe_pct > 0:
            if roe_pct >= 20: scores["roe"] = 3
            elif roe_pct >= 15: scores["roe"] = 2
            elif roe_pct >= 10: scores["roe"] = 1
            elif roe_pct >= 5: scores["roe"] = 0
            else: scores["roe"] = -1
        elif roe_pct is not None and roe_pct <= 0:
            scores["roe"] = -2
        else:
            scores["roe"] = -2

        # 2. 成长性评分
        growth_score = 0
        if yoy_rev_pct is not None and yoy_rev_pct > 0:
            if yoy_rev_pct >= 50: growth_score += 2
            elif yoy_rev_pct >= 30: growth_score += 1
            elif yoy_rev_pct >= 10: growth_score += 0
            else: growth_score -= 1
        elif yoy_rev_pct is not None:
            growth_score -= 2
        else:
            growth_score -= 2
        if yoy_np_pct is not None and yoy_rev_pct is not None and yoy_np_pct > yoy_rev_pct:
            growth_score += 1
        scores["growth"] = max(-3, min(3, growth_score))

        # 3. 估值评分
        industry = industry or "default"
        low_pe, fair_pe, high_pe = SECTOR_PE_RANGES.get(industry, SECTOR_PE_RANGES["default"])
        if pe and pe > 0:
            if pe <= low_pe: scores["valuation"] = 3
            elif pe <= fair_pe: scores["valuation"] = 1
            elif pe <= high_pe: scores["valuation"] = -1
            else: scores["valuation"] = -3
        else:
            scores["valuation"] = -2

        # 4. 资产负债评分
        if balance:
            alr = balance.get("asset_liab_ratio")
            cr = balance.get("current_ratio")
            if alr is not None and cr is not None:
                if alr < 40 and cr > 2: scores["debt"] = 2
                elif alr < 60 and cr > 1: scores["debt"] = 1
                elif alr < 80: scores["debt"] = 0
                else: scores["debt"] = -2
            elif alr is not None:
                if alr < 80: scores["debt"] = 0
                else: scores["debt"] = -1
            else:
                scores["debt"] = 0
        else:
            scores["debt"] = 0

        # 5. 趋势评分（连续多期ROE趋势）
        if len(profit) >= 2:
            roe_trend = []
            for p in profit:
                if p.get("roe"):
                    roe_trend.append(p["roe"])
            if len(roe_trend) >= 2:
                if roe_trend[0] > roe_trend[-1]:
                    scores["trend"] = 2  # 改善
                elif roe_trend[0] < roe_trend[-1]:
                    scores["trend"] = -1  # 恶化
                else:
                    scores["trend"] = 0
        else:
            scores["trend"] = 0

        # 综合基本面评分
        fundamental_score = (
            scores.get("roe", 0) * 0.3 +
            scores.get("growth", 0) * 0.3 +
            scores.get("valuation", 0) * 0.2 +
            scores.get("debt", 0) * 0.1 +
            scores.get("trend", 0) * 0.1
        )
        fundamental_score = round(fundamental_score, 1)

        # 成长类型判断
        g = scores.get("growth", 0)
        r = scores.get("roe", 0)
        if g >= 2 and r >= 2:
            style = "高成长高盈利"
        elif g >= 2:
            style = "高成长"
        elif r >= 2:
            style = "稳定盈利"
        elif fundamental_score < -2:
            style = "亏损/衰退"
        else:
            style = "一般"

        return {
            "code": code,
            "industry": industry or "未知",
            "pe": pe,
            "market_cap": market_cap,
            "roe": roe_pct,
            "yoy_revenue_growth": yoy_rev_pct,
            "yoy_net_profit_growth": yoy_np_pct,
            "asset_liab_ratio": balance.get("asset_liab_ratio") if balance else None,
            "scores": scores,
            "fundamental_score": fundamental_score,
            "style": style,
            "latest_quarter": latest_profit.get("stat_date", ""),
            "data_available": len(profit) > 0,
        }

    def fundamental_bonus(self, code: str, price_data: Dict = None) -> Dict:
        """生成V3评分加分"""
        result = self.analyze(code, price_data)
        score = result.get("fundamental_score", 0)
        bonus = score * 0.3  # 最高±1.5分

        return {
            "fundamental_bonus": round(bonus, 1),
            "fundamental_score": score,
            "style": result.get("style", ""),
            "industry": result.get("industry", ""),
            "roe": result.get("roe"),
            "yoy_growth": result.get("yoy_revenue_growth"),
        }

    def report(self, codes: List[str], price_data_map: Dict = None) -> str:
        """生成基本面分析报告"""
        results = []
        for code in codes:
            pd = price_data_map.get(code) if price_data_map else None
            r = self.analyze(code, pd)
            if r.get("data_available"):
                results.append(r)

        results.sort(key=lambda x: x.get("fundamental_score", -999), reverse=True)

        lines = []
        lines.append(f"🏢 **基本面分析(baostock) | {datetime.now().strftime('%m-%d %H:%M')}**")
        lines.append("")

        if not results:
            lines.append("无数据（需先运行 fetch）")
            return "\n".join(lines)

        # 按风格分类
        styles = {}
        for r in results:
            s = r.get("style", "一般")
            styles.setdefault(s, []).append(r)

        for style_name in ["高成长高盈利", "高成长", "稳定盈利", "一般", "亏损/衰退"]:
            if style_name in styles:
                lines.append(f"**{style_name} ({len(styles[style_name])}只):**")
                for r in styles[style_name][:5]:
                    lines.append(f"  {r['code']} ({r.get('industry','')}) | "
                                 f"ROE={r.get('roe','?')}% | "
                                 f"营收增={r.get('yoy_revenue_growth','?')}% | "
                                 f"评分{r['fundamental_score']:+.1f}")
                lines.append("")

        lines.append(f"📆 数据源: baostock | 最新季度: {results[0].get('latest_quarter','?')}")
        return "\n".join(lines)


# ============= 主入口：更新所有数据 =============

def update_all_fundamentals(codes: List[str] = None):
    """全量更新基本面数据库"""
    if codes is None:
        try:
            from backtest import KlineLoader
            codes = KlineLoader.load_all_codes()
        except:
            codes = []

    fetcher = FundamentalFetcher()

    # 获取行业分类
    n = fetcher.fetch_all_basics()
    print(f"行业分类: {n} 类")

    # 获取财务数据
    total = 0
    for i, code in enumerate(codes):
        try:
            c = fetcher.fetch_financials(code)
            total += c
        except Exception as e:
            pass
        if (i + 1) % 10 == 0:
            print(f"  ... {i+1}/{len(codes)}")

    # 获取K线（补充现有kline_cache）
    from backtest import KlineLoader
    existing = set(KlineLoader.load_all_codes())
    kline_count = 0
    for code in codes:
        if code not in existing:
            try:
                fetcher.fetch_kline_from_bs(code)
                kline_count += 1
            except:
                pass

    fetcher.close()

    return {
        "stock_basics": n,
        "financial_records": total,
        "new_kline_stocks": kline_count,
    }


# ============= 测试 =============

if __name__ == "__main__":
    print("🏢 完整基本面分析层 (baostock)")
    print("=" * 60)

    # 先确保数据存在
    from backtest import KlineLoader
    codes = KlineLoader.load_all_codes()
    print(f"\n已监控股票: {len(codes)} 只")

    # 检查fundamental.db是否有数据
    db = FundamentalDB()
    conn = db._conn()
    c = conn.execute("SELECT COUNT(*) FROM profit_data")
    profit_count = c.fetchone()[0]
    conn.close()

    if profit_count == 0:
        print("\n⚠️ 基本面数据库为空，正在获取...")
        result = update_all_fundamentals(codes[:10])
        print(f"  已获取 {result}")
    else:
        print(f"\n✅ 基本面数据库已有 {profit_count} 条盈利记录")

    # 测试分析
    analyzer = EnhancedFundamentalAnalyzer()
    result = analyzer.analyze("300308")
    print(f"\n中际旭创 基本面分析:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    # 行业分类
    ind = db.get_stock_industry("300308")
    print(f"\n行业: {ind}")

    print("\n✅ 完整基本面分析层就绪")
