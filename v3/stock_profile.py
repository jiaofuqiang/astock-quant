"""
📊 个股画像 + 三维权重矩阵
============================
核心原则：同一策略在不同个股上的表现天差地别，
同一策略在同一只股的不同时间段表现也不同。

功能：
1. 对每只标的，维护4个策略在各时段的独立回测记录
2. 自动计算时效性加权权重（近30天权重>远90天）
3. 每只标的4个策略权重独立，互不影响
4. 策略权重每隔30天自动更新一次

数据存储：SQLite (individual_perf.db)
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

DATA_DIR = "/home/ubuntu/astock/data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "stock_profiles.db")

# ============= SQLite 初始化 =============

INIT_SQL = """
CREATE TABLE IF NOT EXISTS strategy_perf (
    code       TEXT NOT NULL,
    strategy   TEXT NOT NULL,   -- 金叉死叉/MA20回踩/放量突破/RSI
    period     TEXT NOT NULL,   -- 近30天/31-60天/61-90天/91-120天
    total_return REAL,          -- 期间总收益%
    trade_count INTEGER,        -- 交易次数
    win_rate   REAL,            -- 胜率
    avg_hold_days REAL,         -- 平均持仓天数
    volatility REAL,            -- 期间波动率
    record_date TEXT,           -- 记录日期
    PRIMARY KEY (code, strategy, period)
);

CREATE TABLE IF NOT EXISTS strategy_weights (
    code         TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    weight       REAL DEFAULT 1.0,  -- 0.1 ~ 3.0
    last_updated TEXT,
    confidence   REAL DEFAULT 0.5,  -- 0~1 该权重的可信度(数据量越多越高)
    PRIMARY KEY (code, strategy)
);

CREATE TABLE IF NOT EXISTS param_optimization (
    code       TEXT NOT NULL,
    strategy   TEXT NOT NULL,
    params     TEXT NOT NULL,       -- JSON格式参数
    total_return REAL,
    win_rate   REAL,
    trades     INTEGER,
    sharpe     REAL,
    max_dd     REAL,
    opt_date   TEXT,
    PRIMARY KEY (code, strategy, params)
);

CREATE TABLE IF NOT EXISTS profile_meta (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    sector      TEXT,
    market_cap  TEXT,        -- 大/中/小
    updated_at  TEXT
);
"""


class StockProfileDB:
    """个股画像数据库管理"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(INIT_SQL)
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def update_strategy_perf(self, code: str, strategy: str, period: str,
                             total_return: float, trade_count: int,
                             win_rate: float, avg_hold_days: float,
                             volatility: float):
        """更新某只股某策略在某时段的表现"""
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO strategy_perf
            (code, strategy, period, total_return, trade_count,
             win_rate, avg_hold_days, volatility, record_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, strategy, period,
              round(total_return, 2), trade_count,
              round(win_rate, 1), round(avg_hold_days, 1),
              round(volatility, 2), datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()

    def get_strategy_perf(self, code: str, strategy: str) -> Dict[str, Dict]:
        """获取某只股某策略在各时段的表现"""
        conn = self._conn()
        c = conn.execute("""
            SELECT period, total_return, trade_count, win_rate,
                   avg_hold_days, volatility, record_date
            FROM strategy_perf
            WHERE code=? AND strategy=?
            ORDER BY
                CASE period
                    WHEN '近30天' THEN 0
                    WHEN '31-60天' THEN 1
                    WHEN '61-90天' THEN 2
                    WHEN '91-120天' THEN 3
                    ELSE 4
                END
        """, (code, strategy))
        results = {}
        for row in c.fetchall():
            results[row[0]] = {
                "return": row[1],
                "trades": row[2],
                "win_rate": row[3],
                "avg_hold": row[4],
                "volatility": row[5],
                "date": row[6],
            }
        conn.close()
        return results

    def update_weight(self, code: str, strategy: str, weight: float, confidence: float = 0.5):
        """更新个股×策略的权重"""
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO strategy_weights
            (code, strategy, weight, last_updated, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (code, strategy, round(weight, 3),
              datetime.now().isoformat(), round(confidence, 2)))
        conn.commit()
        conn.close()

    def get_weight(self, code: str, strategy: str) -> Tuple[float, float]:
        """获取个股×策略的权重和置信度"""
        conn = self._conn()
        c = conn.execute("""
            SELECT weight, confidence FROM strategy_weights
            WHERE code=? AND strategy=?
        """, (code, strategy))
        row = c.fetchone()
        conn.close()
        return (row[0], row[1]) if row else (1.0, 0.0)

    def get_all_weights(self, code: str) -> Dict[str, float]:
        """获取某只股所有策略的权重"""
        conn = self._conn()
        c = conn.execute("""
            SELECT strategy, weight FROM strategy_weights WHERE code=?
        """, (code,))
        weights = {row[0]: row[1] for row in c.fetchall()}
        conn.close()
        return weights

    def save_param_optimization(self, code: str, strategy: str, params: Dict,
                                 total_return: float, win_rate: float,
                                 trades: int, sharpe: float, max_dd: float):
        """保存参数优化结果"""
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO param_optimization
            (code, strategy, params, total_return, win_rate,
             trades, sharpe, max_dd, opt_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, strategy, json.dumps(params, ensure_ascii=False),
              round(total_return, 2), round(win_rate, 1),
              trades, round(sharpe, 3), round(max_dd, 2),
              datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_best_params(self, code: str, strategy: str) -> Optional[Dict]:
        """获取某股某策略的最优参数组合"""
        conn = self._conn()
        c = conn.execute("""
            SELECT params, total_return, trades, win_rate
            FROM param_optimization
            WHERE code=? AND strategy=?
            ORDER BY total_return DESC
            LIMIT 1
        """, (code, strategy))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                "params": json.loads(row[0]),
                "total_return": row[1],
                "trades": row[2],
                "win_rate": row[3],
            }
        return None

    def update_profile_meta(self, code: str, name: str = "",
                             sector: str = "", market_cap: str = ""):
        """更新个股元数据"""
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO profile_meta
            (code, name, sector, market_cap, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, name, sector, market_cap, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_profile(self, code: str) -> Dict:
        """获取完整个股画像"""
        profile = {"code": code, "weights": {}, "perf": {}, "params": {}}

        # 权重
        weights = self.get_all_weights(code)
        profile["weights"] = weights

        # 各策略在各时段的表现
        for strategy in ["金叉死叉", "MA20回踩", "放量突破", "RSI"]:
            perf = self.get_strategy_perf(code, strategy)
            if perf:
                profile["perf"][strategy] = perf
            # 最优参数
            best_params = self.get_best_params(code, strategy)
            if best_params:
                profile["params"][strategy] = best_params

        return profile

    def needs_update(self, code: str) -> bool:
        """判断是否需要更新权重（7天以上未更新）"""
        conn = self._conn()
        c = conn.execute("""
            SELECT last_updated FROM strategy_weights
            WHERE code=? LIMIT 1
        """, (code,))
        row = c.fetchone()
        conn.close()
        if not row:
            return True
        try:
            last = datetime.fromisoformat(row[0])
            return (datetime.now() - last).days >= 7
        except:
            return True

    def get_all_codes(self) -> List[str]:
        """获取所有有记录的股票代码"""
        conn = self._conn()
        c = conn.execute("SELECT DISTINCT code FROM strategy_perf")
        codes = [row[0] for row in c.fetchall()]
        conn.close()
        return codes

    def get_codes_needing_update(self) -> List[str]:
        """获取需要更新权重的股票"""
        conn = self._conn()
        c = conn.execute("""
            SELECT s.code FROM strategy_weights s
            WHERE s.last_updated < datetime('now', '-7 days')
               OR s.last_updated IS NULL
        """)
        codes = [row[0] for row in c.fetchall()]
        conn.close()
        return codes


# ============= 时效性权重计算 =============

def time_decay_weight(days_ago: int, half_life: int = 30) -> float:
    """时间衰减函数: 半衰期30天"""
    return 2 ** (-days_ago / half_life)


PERIOD_MAP = {
    "近30天": (1, 30),
    "31-60天": (31, 60),
    "61-90天": (61, 90),
    "91-120天": (91, 120),
}

PERIOD_ORDER = ["近30天", "31-60天", "61-90天", "91-120天"]


def calculate_adaptive_weight(strategy_perf: Dict[str, Dict]) -> Tuple[float, float]:
    """
    从策略在各时段的表现→计算自适应权重

    Args:
        strategy_perf: { 近30天: {return, trades, win_rate}, ... }

    Returns:
        (weight, confidence)
        weight: 0.1 ~ 3.0
        confidence: 0 ~ 1
    """
    if not strategy_perf:
        return 1.0, 0.0

    # 加权收益（越近权重越高）
    weighted_return = 0.0
    total_weight = 0.0
    total_trades = 0

    for period in PERIOD_ORDER:
        if period not in strategy_perf:
            continue
        perf = strategy_perf[period]
        ret = perf.get("return", 0) or 0
        trades = perf.get("trades", 0) or 0
        # 该时段的中位数天数
        start, end = PERIOD_MAP.get(period, (1, 30))
        mid_day = (start + end) / 2
        w = time_decay_weight(mid_day, half_life=45)

        weighted_return += ret * w
        total_weight += w
        total_trades += trades

    avg_weighted_return = weighted_return / total_weight if total_weight > 0 else 0

    # 把收益率映射到权重
    # 基准: 0%收益 → 1.0x
    # +5%收益 → 1.3x
    # -5%收益 → 0.7x
    # 最大 3.0x, 最小 0.1x
    raw_weight = 1.0 + avg_weighted_return * 0.06
    weight = max(0.1, min(3.0, raw_weight))

    # 置信度: 交易次数越多置信度越高
    max_confidence_trades = 20  # 20笔交易以上给满置信度
    confidence = min(1.0, total_trades / max_confidence_trades)

    return round(weight, 2), round(confidence, 2)


# ============= 全局批量更新 =============

def update_all_weights():
    """
    对所有有数据的股票，重新计算所有策略的权重
    调用时机：每日收盘后/每周一次
    """
    db = StockProfileDB()
    codes = db.get_all_codes()

    updated = 0
    for code in codes:
        for strategy in ["金叉死叉", "MA20回踩", "放量突破", "RSI"]:
            perf = db.get_strategy_perf(code, strategy)
            if not perf:
                continue
            weight, confidence = calculate_adaptive_weight(perf)
            old_weight, old_conf = db.get_weight(code, strategy)
            # 只有权重变化或置信度显著提高时才更新
            if abs(weight - old_weight) > 0.05 or confidence > old_conf + 0.1:
                db.update_weight(code, strategy, weight, confidence)
                updated += 1

    return updated


def populate_profiles():
    """从K线数据库回填个股画像"""
    from backtest import KlineLoader, GoldenCrossStrategy, MaBounceStrategy, \
        VolumeBreakStrategy, OversoldBounceStrategy, BacktestEngine

    loader = KlineLoader()
    codes = loader.load_all_codes()
    engine = BacktestEngine()
    db = StockProfileDB()

    strategies = {
        "金叉死叉": GoldenCrossStrategy,
        "MA20回踩": MaBounceStrategy,
        "放量突破": VolumeBreakStrategy,
        "RSI": OversoldBounceStrategy,
    }

    total = 0
    for code in codes:
        kline = loader.load_kline(code)
        if len(kline) < 60:
            continue

        for sname, sclass in strategies.items():
            strategy = sclass()

            for pname, (start_offset, end_offset) in PERIOD_MAP.items():
                # 取对应时段的K线
                seg = kline[-end_offset:-start_offset+1] if end_offset <= len(kline) else []
                if len(seg) < 20:
                    continue

                result = engine.run(seg, strategy)
                if result.get("error"):
                    continue

                ret = result.get("total_return_pct", 0)
                trades = result.get("total_trades", 0)
                win_rate = result.get("win_rate", 0)
                avg_hold = result.get("avg_hold_days", 0)

                # 波动率估算
                closes = [k["close"] for k in seg]
                volatility = (max(closes) - min(closes)) / (sum(closes)/len(closes)) * 100 if closes else 3

                db.update_strategy_perf(
                    code, sname, pname,
                    ret, trades, win_rate, avg_hold, volatility
                )
                total += 1

        # 更新权重
        for sname in strategies:
            perf = db.get_strategy_perf(code, sname)
            if perf:
                weight, conf = calculate_adaptive_weight(perf)
                db.update_weight(code, sname, weight, conf)

    print(f"✅ 已回填 {total} 条策略表现记录")
    return total


# ============= 测试 =============

if __name__ == "__main__":
    print("📊 个股画像 + 三维权重矩阵")
    print("=" * 60)

    db = StockProfileDB()

    # 测试权重计算
    test_perf = {
        "近30天": {"return": 12.5, "trades": 6, "win_rate": 66.7, "avg_hold": 5, "volatility": 8.0},
        "31-60天": {"return": 3.2, "trades": 4, "win_rate": 50.0, "avg_hold": 7, "volatility": 6.0},
        "61-90天": {"return": -2.1, "trades": 3, "win_rate": 33.3, "avg_hold": 6, "volatility": 5.0},
        "91-120天": {"return": 8.0, "trades": 5, "win_rate": 60.0, "avg_hold": 5, "volatility": 7.0},
    }

    weight, conf = calculate_adaptive_weight(test_perf)
    print(f"\n测试权重计算:")
    print(f"  过去120天平均加权收益: +{(12.5*1.0 + 3.2*0.5 + -2.1*0.25 + 8.0*0.125)/1.875:.1f}%")
    print(f"  计算权重: {weight}x")
    print(f"  置信度: {conf}")
    print(f"  预期: 近30天大涨 +12.5% → 权重应 > 1.3x")

    print("\n✅ stock_profile.py 就绪")
