"""
🔗 V3.2 关联分析引擎（龙头→跟风股传导）
============================================
核心原则：龙头股的涨跌不是孤立的，它会按一定模式传导到同板块跟风股。

思路：
1. 对每个板块，找出"龙头股"（历史涨幅最大、最先涨的）
2. 学习龙头涨→跟风股涨的时序模式和强度
3. 当龙头触发信号时，自动计算跟风股的反应预期
4. 预测传导概率（50%~90%）和传导时间（0~3天）

这解决了一个核心问题：
  如果龙头已经大涨不敢追，但预期它会传导到还没涨的跟风股，
  就可以"提前埋伏"。
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import KlineLoader, calc_ma

DATA_DIR = "/home/ubuntu/astock/data"
DB_PATH = os.path.join(DATA_DIR, "correlation.db")

# ============= 板块定义 =============

SECTOR_GROUPS = {
    "光模块": ["300308", "300394", "300502"],
    "AI算力": ["603019", "000977"],
    "半导体": ["002371", "300661", "688981", "688041", "688012",
               "688072", "688126", "300782", "600745", "300054",
               "600703", "002156"],
    "机器人": ["300124", "688017", "002230", "300660", "002747"],
    "新能源": ["300750", "002594", "300014", "002460", "002812",
               "300124", "002709", "600438"],
    "军工":   ["600760", "600893", "600760", "000768"],
    "证券":   ["300059", "600030"],
    "医药":   ["300760", "600276"],
    "信创":   ["002230", "688041"],
    "白酒":   ["000568", "000858", "000596", "002304"],
    "银行":   ["601398", "601939", "601288"],
    "地产":   ["001979", "600048"],
    "汽车":   ["600104", "000625"],
    "周期":   ["600585", "600019", "600028", "000630"],
}


@dataclass
class CorrelationRecord:
    """龙头→跟风联动记录"""
    leader_code: str
    follower_code: str
    sector: str
    date: str
    leader_chg: float     # 龙头当日涨幅
    follower_lag0: float  # 跟风当天涨幅
    follower_lag1: float  # 跟风1天后涨幅
    follower_lag2: float  # 跟风2天后涨幅
    follower_lag3: float  # 跟风3天后涨幅
    max_follower_chg: float  # 后3天内最大涨幅
    correlation: float = 0.0  # 联动强度


class CorrelationEngine:
    """
    关联分析引擎

    1. 识别每个板块的"龙头"（基于近期走势强度）
    2. 学习龙头涨→跟风股的传导模式
    3. 输出传导概率和预期涨幅
    """

    def __init__(self):
        self.loader = KlineLoader()
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS correlation (
                leader_code TEXT,
                follower_code TEXT,
                sector TEXT,
                date TEXT,
                leader_chg REAL,
                follower_lag0 REAL,
                follower_lag1 REAL,
                follower_lag2 REAL,
                follower_lag3 REAL,
                max_follower_chg REAL,
                correlation REAL,
                PRIMARY KEY (leader_code, follower_code, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leader_rankings (
                code TEXT PRIMARY KEY,
                sector TEXT,
                name TEXT DEFAULT '',
                strength REAL,
                rank INTEGER,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(DB_PATH)

    def identify_leaders(self, lookback_days: int = 60) -> Dict[str, List[Tuple[str, float]]]:
        """
        识别每个板块的龙头

        Returns:
            { "光模块": [("300308", 12.5), ("300502", 8.3)], ... }
        """
        conn = self._conn()
        leaders = {}
        for sector, codes in SECTOR_GROUPS.items():
            strengths = []
            for code in codes:
                kline = self.loader.load_kline(code)
                if len(kline) < lookback_days:
                    continue
                closes = [k["close"] for k in kline[-lookback_days:]]
                if len(closes) < 2:
                    continue
                # 强度 = 近期涨幅 + 趋势一致性
                total_chg = (closes[-1] - closes[0]) / closes[0] * 100
                # 计算趋势一致性
                ups = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
                consistency = ups / len(closes) * 100 if len(closes) > 1 else 0
                strength = total_chg * 0.7 + consistency * 0.3
                strengths.append((code, round(strength, 1)))

            strengths.sort(key=lambda x: x[1], reverse=True)
            leaders[sector] = strengths[:3]  # 每个板块前3名

            # 保存到数据库
            for rank, (code, strength) in enumerate(strengths[:3]):
                conn.execute("""
                    INSERT OR REPLACE INTO leader_rankings
                    (code, sector, strength, rank, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (code, sector, strength, rank, datetime.now().isoformat()))

        conn.commit()
        conn.close()
        return leaders

    def compute_correlation(self, code_a: str, code_b: str,
                             lookback: int = 60) -> Dict:
        """
        计算两只股票的相关性

        Returns:
            {
                "pearson": 0.85,  皮尔逊相关系数
                "lag_analysis": {...} 滞后相关性
            }
        """
        kline_a = self.loader.load_kline(code_a, lookback)
        kline_b = self.loader.load_kline(code_b, lookback)

        if len(kline_a) < 20 or len(kline_b) < 20:
            return {"pearson": 0, "lag_analysis": {}}

        # 对齐日期
        date_map_a = {k["date"]: k["close"] for k in kline_a}
        date_map_b = {k["date"]: k["close"] for k in kline_b}
        common_dates = sorted(set(date_map_a.keys()) & set(date_map_b.keys()))
        if len(common_dates) < 20:
            return {"pearson": 0, "lag_analysis": {}}

        prices_a = [date_map_a[d] for d in common_dates]
        prices_b = [date_map_b[d] for d in common_dates]

        # 皮尔逊
        n = len(prices_a)
        mean_a = sum(prices_a) / n
        mean_b = sum(prices_b) / n
        cov = sum((prices_a[i] - mean_a) * (prices_b[i] - mean_b) for i in range(n))
        var_a = sum((prices_a[i] - mean_a) ** 2 for i in range(n))
        var_b = sum((prices_b[i] - mean_b) ** 2 for i in range(n))
        pearson = cov / ((var_a * var_b) ** 0.5) if var_a > 0 and var_b > 0 else 0

        # 滞后相关性：B在A涨之后N天的表现
        lag_analysis = {}
        for lag in [0, 1, 2, 3]:
            if n <= lag:
                continue
            b_lagged = prices_b[lag:]
            a_aligned = prices_a[:n-lag]
            if len(b_lagged) < 10:
                continue
            ml = sum(b_lagged) / len(b_lagged)
            cov_lag = sum((a_aligned[i] - mean_a) * (b_lagged[i] - ml) for i in range(len(a_aligned)))
            var_bl = sum((b_lagged[i] - ml) ** 2 for i in range(len(b_lagged)))
            lag_corr = cov_lag / ((var_a * var_bl) ** 0.5) if var_a > 0 and var_bl > 0 else 0
            lag_analysis[f"lag_{lag}d"] = round(lag_corr, 3)

        return {
            "pearson": round(pearson, 3),
            "lag_analysis": lag_analysis,
            "common_days": n,
        }

    def build_correlation_matrix(self, sector: str = None) -> Dict:
        """
        构建板块内所有股票间的关联矩阵

        Args:
            sector: 指定板块，None则全部

        Returns:
            { "300308": { "300394": {"pearson": 0.85, ...}, ... }, ... }
        """
        codes = []
        if sector:
            codes = SECTOR_GROUPS.get(sector, [])
        else:
            for sc in SECTOR_GROUPS.values():
                codes.extend(sc)
        codes = list(set(codes))

        matrix = {}
        for i, c1 in enumerate(codes):
            matrix[c1] = {}
            for c2 in codes:
                if c1 >= c2:  # 只算一次
                    continue
                corr = self.compute_correlation(c1, c2)
                matrix[c1][c2] = corr
                if c2 not in matrix:
                    matrix[c2] = {}
                matrix[c2][c1] = corr  # 对称填充

        return matrix

    def learn_leader_follower(self, leader_code: str, follower_code: str,
                               sector: str, lookback: int = 120):
        """
        学习龙头→跟风股的传导模式

        遍历历史，记录龙头大涨那天，跟风股后几天的表现
        """
        kline_leader = self.loader.load_kline(leader_code, lookback)
        kline_follower = self.loader.load_kline(follower_code, lookback)

        if len(kline_leader) < 40 or len(kline_follower) < 40:
            return [], 0, 0

        # 对齐
        dates_l = {k["date"]: k for k in kline_leader}
        dates_f = {k["date"]: k for k in kline_follower}
        common = sorted(set(dates_l.keys()) & set(dates_f.keys()))
        if len(common) < 20:
            return [], 0, 0

        records = []
        for i, d in enumerate(common):
            if i == 0:
                continue
            prev_l = dates_l[common[i-1]]["close"]
            cur_l = dates_l[d]["close"]
            leader_chg = (cur_l - prev_l) / prev_l * 100

            if leader_chg < 4:  # 只记录龙头大涨>=4%的交易日
                continue

            prev_f = dates_f[common[i-1]]["close"]
            cur_f = dates_f[d]["close"]
            follower_chg_0 = (cur_f - prev_f) / prev_f * 100

            # 跟风后1-3天
            follower_chg_1 = 0
            follower_chg_2 = 0
            follower_chg_3 = 0
            max_chg = follower_chg_0

            for lag in [1, 2, 3]:
                idx = common.index(d) + lag
                if idx < len(common):
                    f_d = common[idx]
                    f_price = dates_f[f_d]["close"]
                    lag_chg = (f_price - cur_f) / cur_f * 100
                    if lag == 1:
                        follower_chg_1 = lag_chg
                    elif lag == 2:
                        follower_chg_2 = lag_chg
                    elif lag == 3:
                        follower_chg_3 = lag_chg
                    if lag_chg > max_chg:
                        max_chg = lag_chg

            rec = CorrelationRecord(
                leader_code=leader_code,
                follower_code=follower_code,
                sector=sector, date=d,
                leader_chg=round(leader_chg, 2),
                follower_lag0=round(follower_chg_0, 2),
                follower_lag1=round(follower_chg_1, 2),
                follower_lag2=round(follower_chg_2, 2),
                follower_lag3=round(follower_chg_3, 2),
                max_follower_chg=round(max_chg, 2),
            )
            records.append(rec)

        # 统计联动模式
        if not records:
            return [], 0, 0

        immediate_follows = sum(1 for r in records if r.follower_lag0 > 1)
        delayed_follows = sum(1 for r in records
                               if r.follower_lag0 <= 1 and r.max_follower_chg > 1)
        total = len(records)

        immediate_rate = immediate_follows / total * 100 if total > 0 else 0
        delayed_rate = (immediate_follows + delayed_follows) / total * 100 if total > 0 else 0

        # 平均跟风涨幅
        avg_lag0 = sum(r.follower_lag0 for r in records) / total
        avg_max = sum(r.max_follower_chg for r in records) / total

        # 保存到数据库
        conn = self._conn()
        for r in records:
            conn.execute("""
                INSERT OR REPLACE INTO correlation
                (leader_code, follower_code, sector, date, leader_chg,
                 follower_lag0, follower_lag1, follower_lag2, follower_lag3,
                 max_follower_chg, correlation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r.leader_code, r.follower_code, r.sector, r.date,
                  r.leader_chg, r.follower_lag0, r.follower_lag1,
                  r.follower_lag2, r.follower_lag3, r.max_follower_chg,
                  round(immediate_rate, 1)))
        conn.commit()
        conn.close()

        return records, immediate_rate, delayed_rate

    def learn_all_pairs(self):
        """对所有板块内龙头-跟风对学习关联模式"""
        results = {}
        for sector, codes in SECTOR_GROUPS.items():
            if len(codes) < 2:
                continue

            # 识别龙头
            leaders = self.identify_leaders()
            top_leaders = leaders.get(sector, [])
            if not top_leaders:
                continue

            leader_code = top_leaders[0][0]

            for code in codes:
                if code == leader_code:
                    continue
                try:
                    records, imm, delay = self.learn_leader_follower(
                        leader_code, code, sector)
                    results[f"{leader_code}→{code}"] = {
                        "sector": sector,
                        "leader": leader_code,
                        "follower": code,
                        "immediate_follow_rate": round(imm, 1),
                        "delay_follow_rate": round(delay, 1),
                        "total_events": len(records),
                    }
                except Exception as e:
                    print(f"  ⚠️ {leader_code}→{code}: {e}")

        return results

    def predict_follower_reaction(self, leader_code: str, leader_chg: float,
                                    sector: str) -> Dict:
        """
        预测当龙头涨跌时，跟风股的反应

        Args:
            leader_code: 龙头代码
            leader_chg: 龙头今日涨幅%
            sector: 板块名

        Returns:
            {
                "follower_predictions": [
                    {"code": "300394", "expected_chg": 2.5,
                     "confidence": "high", "timeframe": "即时/1天内"}
                ],
                "sector_avg": 2.1
            }
        """
        conn = self._conn()
        predictions = []

        for code in SECTOR_GROUPS.get(sector, []):
            if code == leader_code:
                continue

            # 从数据库查该对的关联记录
            c = conn.execute("""
                SELECT leader_chg, follower_lag0, follower_lag1,
                       follower_lag2, max_follower_chg
                FROM correlation
                WHERE leader_code=? AND follower_code=?
                ORDER BY date DESC LIMIT 50
            """, (leader_code, code))
            rows = c.fetchall()
            if len(rows) < 3:
                continue

            # 计算历史均值
            avg_lag0 = sum(r[1] for r in rows) / len(rows)
            avg_max = sum(r[4] for r in rows) / len(rows)

            # 当前龙头涨幅 vs 历史上的龙头涨幅比例
            avg_leader_chg = sum(r[0] for r in rows) / len(rows) if rows else 5
            ratio = leader_chg / avg_leader_chg if avg_leader_chg > 0 else 1

            expected_immediate = avg_lag0 * ratio
            expected_max = avg_max * ratio

            # 置信度
            if len(rows) >= 10:
                confidence = "high"
            elif len(rows) >= 5:
                confidence = "medium"
            else:
                confidence = "low"

            predictions.append({
                "code": code,
                "expected_immediate_chg": round(expected_immediate, 1),
                "expected_max_chg": round(expected_max, 1),
                "confidence": confidence,
                "samples": len(rows),
                "timeframe": "当日" if abs(expected_immediate) > 0.5 else "1-3天内",
            })

        conn.close()

        predictions.sort(key=lambda x: abs(x["expected_max_chg"]), reverse=True)

        avg_exp = sum(p["expected_max_chg"] for p in predictions) / len(predictions) if predictions else 0

        return {
            "leader": leader_code,
            "leader_chg": leader_chg,
            "sector": sector,
            "follower_predictions": predictions,
            "sector_avg_expected": round(avg_exp, 1),
            "total_followers": len(predictions),
        }

    def get_leader_of_sector(self, sector: str) -> Optional[str]:
        """获取板块当前龙头"""
        conn = self._conn()
        c = conn.execute("""
            SELECT code FROM leader_rankings
            WHERE sector=? AND rank=0
            ORDER BY updated_at DESC LIMIT 1
        """, (sector,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None

    def correlation_report(self, sector: str = None) -> str:
        """生成关联分析报告"""
        lines = []
        lines.append("🔗 **板块联动分析**")
        lines.append("")

        if sector:
            sectors = [sector]
        else:
            sectors = list(SECTOR_GROUPS.keys())

        conn = self._conn()

        for sec in sectors[:5]:  # 最多5个板块
            codes = SECTOR_GROUPS.get(sec, [])
            if len(codes) < 2:
                continue

            # 查关联统计
            c = conn.execute("""
                SELECT leader_code, follower_code,
                       AVG(immediate_rate), COUNT(*)
                FROM (
                    SELECT leader_code, follower_code,
                           CASE WHEN follower_lag0 > 1 THEN 1 ELSE 0 END as immediate_rate
                    FROM correlation WHERE sector=?
                ) GROUP BY leader_code, follower_code
                ORDER BY COUNT(*) DESC LIMIT 5
            """, (sec,))
            rows = c.fetchall()

            if rows:
                lines.append(f"**{sec}:**")
                for r in rows[:3]:
                    lines.append(f"  {r[0]}→{r[1]}: 即时联动率{r[2]:.0f}% ({r[3]}次历史)")
                lines.append("")

        conn.close()
        lines.append("📆 根据历史数据学习，龙头大涨后跟风股通常1-3天内反应")
        return "\n".join(lines)


# ============= 测试 =============

if __name__ == "__main__":
    print("🔗 关联分析引擎")
    print("=" * 60)

    engine = CorrelationEngine()

    # 识别龙头
    print("\n识别板块龙头:")
    leaders = engine.identify_leaders()
    for sector, tops in sorted(leaders.items()):
        names = [f"{c}({s}分)" for c, s in tops]
        print(f"  {sector}: {', '.join(names[:2])}")

    # 关联矩阵
    print("\n光模块关联矩阵:")
    matrix = engine.build_correlation_matrix("光模块")
    for c1 in sorted(matrix.keys()):
        for c2 in sorted(matrix[c1].keys()):
            if c1 < c2:
                p = matrix[c1][c2].get("pearson", 0)
                bar = "█" * int(abs(p) * 10)
                print(f"  {c1} ↔ {c2}: 相关系数{p:.2f} {bar}")

    print("\n✅ correlation.py 就绪")
