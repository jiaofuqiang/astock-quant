#!/usr/bin/env python3
"""
⚡ 惯性面量化模型 v1.0

"趋势走了多远了？还能走多远？什么信号说明趋势要断了？"

核心逻辑：
  每只股票的趋势就像一辆行驶的列车——
  ① 已经跑了多远（累计涨幅/切线角度/能量消耗）
  ② 还剩多少燃料（换手率衰减/成交量萎缩/动量加速度减速）
  ③ 什么信号说明要脱轨（量价背离/趋势线破位/加速赶顶）

3维12因子惯性量化体系：
  ┌─ 维度A：已走里程（趋势走了多远）
  │  ① 累计涨幅比 (chg_60d / chg_250d)
  │  ② 切线角度 (20日均线斜率)
  │  ③ 价格位置 (60日分位数)
  │  ④ 动量加速度 (chg_10d - chg_5d)
  │
  ├─ 维度B：剩余燃料（还能走多远）
  │  ⑤ 换手率健康度 (当前换手/均值换手)
  │  ⑥ 量能趋势 (5日均量 vs 20日均量比)
  │  ⑦ 动量衰减率 (后5日/前5日动量比)
  │  ⑧ 持仓成本集中度 (筹码密集区距离)
  │
  └─ 维度C：脱轨信号（趋势什么时候会断）
      ⑨ 量价背离检测 (价涨量缩/价跌量增)
     ⑩ 趋势线破位 (收盘跌破20/60日线)
     ⑪ 加速赶顶 (3日涨幅>10%且RSI>80)
     ⑫ 资金背离 (超大单流出但股价上涨)

输出：
  - 趋势评级（主升/健康/衰减/末期）
  - 惯性续航评分
  - 拐点预警信号
  - 建议操作

用法：
  python3 scripts/momentum.py --code 603986        # 单只个股分析
  python3 scripts/momentum.py --sector AI芯片       # 板块内TOP5分析
  python3 scripts/momentum.py --scan                # 全量扫描涨停/大涨标的
  python3 scripts/momentum.py --watch               # 持续监控模式
"""

import os, sys, json, sqlite3, time, math, re
from datetime import datetime, timedelta
from collections import defaultdict, deque

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'kline_cache.db')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# ============================================================
# K线数据加载
# ============================================================

class KlineLoader:
    """从SQLite加载K线数据"""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def load_stock(self, code: str, max_days: int = 250) -> list:
        """加载单只股票K线，最新在前"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT date, open, close, high, low, volume FROM kline WHERE code=? ORDER BY date DESC LIMIT ?",
            (code, max_days)
        )
        rows = cur.fetchall()
        conn.close()
        return [{
            'date': r[0], 'open': r[1], 'close': r[2],
            'high': r[3], 'low': r[4], 'volume': r[5]
        } for r in rows]

    def load_multi(self, codes: list, max_days: int = 250) -> dict:
        """批量加载"""
        result = {}
        for code in codes:
            result[code] = self.load_stock(code, max_days)
        return result


# ============================================================
# 实时行情获取
# ============================================================

def fetch_realtime(code: str) -> dict:
    """获取实时行情"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    import subprocess
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
             f'https://qt.gtimg.cn/q={prefix}{code}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, _ = proc.communicate(timeout=12)
        if proc.returncode != 0:
            return {}
        txt = out.decode('gbk', errors='replace')
        m = re.search(r'\"(.*)\"', txt)
        if not m:
            return {}
        parts = m.group(1).split('~')
        if len(parts) < 48:
            return {}
        return {
            'code': code, 'name': parts[1],
            'price': float(parts[3]) if parts[3] and parts[3] != '-' else 0,
            'change_pct': float(parts[32]) if parts[32] and parts[32] != '-' else 0,
            'volume': float(parts[6]) if parts[6] and parts[6] != '-' else 0,
            'turnover': float(parts[38]) if parts[38] and parts[38] != '-' else 0,
            'pe': float(parts[39]) if parts[39] and parts[39] != '-' else 0,
            'market_cap': float(parts[44]) if parts[44] and parts[44] != '-' else 0,
            'high': float(parts[33]) if parts[33] and parts[33] != '-' else 0,
            'low': float(parts[34]) if parts[34] and parts[34] != '-' else 0,
            'amount': float(parts[37]) if parts[37] and parts[37] != '-' else 0,
        }
    except Exception:
        return {}


# ============================================================
# 惯性面量化引擎
# ============================================================

class MomentumEngine:
    """
    惯性面量化引擎

    对单只股票的K线数据进行3维12因子分析，
    输出趋势评级、续航评分和拐点预警。
    """

    def __init__(self):
        self.loader = KlineLoader()

    def analyze(self, code: str, klines: list = None, rt: dict = None) -> dict:
        """
        完整分析入口

        Args:
            code: 股票代码
            klines: K线列表（最新在前），如果不传则自动加载
            rt: 实时行情数据，如果不传则自动获取

        Returns:
            dict: 包含3维评分、趋势评级、拐点信号
        """
        if klines is None:
            klines = self.loader.load_stock(code, 250)
        if rt is None:
            rt = fetch_realtime(code)

        if not klines:
            return {'error': f'{code}: 无K线数据', 'code': code}

        # 提取价格序列（最新在前）
        closes = [k['close'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        volumes = [k['volume'] for k in klines]
        opens = [k['open'] for k in klines]
        dates = [k['date'] for k in klines]
        n = len(closes)

        current_close = rt.get('price', closes[0]) if rt else closes[0]
        current_chg = rt.get('change_pct', 0) if rt else 0

        # ========== 维度A：已走里程 ==========
        dim_a = self._dim_a(closes, volumes, n)

        # ========== 维度B：剩余燃料 ==========
        dim_b = self._dim_b(closes, volumes, highs, lows, n, current_chg)

        # ========== 维度C：脱轨信号 ==========
        dim_c = self._dim_c(closes, volumes, opens, highs, lows, n)

        # 综合惯性评分
        inertia_score = dim_a['score'] * 0.30 + dim_b['score'] * 0.40 + dim_c['safety_score'] * 0.30

        # 趋势评级
        trend_rating, signal = self._rate_trend(inertia_score, dim_c, dim_a, dim_b)

        # 建议操作
        action = self._suggest_action(trend_rating, dim_c, current_chg)

        result = {
            'code': code,
            'name': rt.get('name', '') if rt else '',
            'price': current_close,
            'change_pct': current_chg,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'trend_rating': trend_rating,
            'inertia_score': round(inertia_score, 1),
            'dim_a_distance': dim_a,
            'dim_b_fuel': dim_b,
            'dim_c_signal': dim_c,
            'action': action,
            'latest_date': dates[0] if dates else '',
        }
        return result

    def _dim_a(self, closes: list, volumes: list, n: int) -> dict:
        """维度A：已走里程 — 趋势走了多远"""
        # 因子① 累计涨幅比：60日/250日涨幅比例
        if n >= 60 and closes[59] > 0 and n >= 250 and closes[249] > 0:
            chg_60d = (closes[0] - closes[59]) / closes[59] * 100
            chg_250d = (closes[0] - closes[min(249, n-1)]) / closes[min(249, n-1)] * 100
            chg_ratio = chg_60d / chg_250d if chg_250d != 0 else 0.5
        elif n >= 60 and closes[59] > 0:
            chg_60d = (closes[0] - closes[59]) / closes[59] * 100
            chg_250d = chg_60d
            chg_ratio = 1.0
        else:
            chg_60d = 0
            chg_250d = 0
            chg_ratio = 0.5

        # 最近60日涨幅
        chg_60d = round(chg_60d, 2) if 'chg_60d' in dir() else 0

        # 因子② 切线角度：20日均线斜率（角度制）
        if n >= 20:
            ma20_now = sum(closes[:20]) / 20
            ma20_prev = sum(closes[20:40]) / 20 if n >= 40 else sum(closes[20:min(40, n)]) / min(20, n-20)
            slope_pct = (ma20_now - ma20_prev) / ma20_prev * 100 if ma20_prev > 0 else 0
            # 角度制 = atan(斜率%)
            slope_angle = math.degrees(math.atan(slope_pct / 100)) if slope_pct != 0 else 0
        else:
            slope_pct = 0
            slope_angle = 0

        # 因子③ 价格位置：60日分位数
        if n >= 60:
            min_60 = min(closes[:60])
            max_60 = max(closes[:60])
            price_pos = (closes[0] - min_60) / (max_60 - min_60) if (max_60 - min_60) > 0 else 0.5
        else:
            price_pos = 0.5

        # 因子④ 动量加速度：chg_10d - chg_5d
        if n >= 10 and closes[4] > 0 and closes[9] > 0:
            chg_5d = (closes[0] - closes[4]) / closes[4] * 100 if closes[4] > 0 else 0
            chg_10d = (closes[0] - closes[9]) / closes[9] * 100 if closes[9] > 0 else 0
            accel = chg_10d - chg_5d
        else:
            chg_5d = 0
            chg_10d = 0
            accel = 0

        # 维度A评分（值越高说明趋势已走了很远了，越接近终点）
        # 累计涨幅大(高分)、切线陡(高分)、价格位置高(高分)、加速度减速(高分)
        a_score = 0
        # 累计60日涨幅>30% → 已走不少里程
        if chg_60d > 60:
            a_score += 40
        elif chg_60d > 30:
            a_score += 30
        elif chg_60d > 15:
            a_score += 20
        elif chg_60d > 5:
            a_score += 10
        # 切线角度>20度 → 陡峭
        a_score += min(25, max(0, slope_angle)) / 20 * 25 if slope_angle > 0 else 0
        # 价格位置>80%分位 → 高位
        if price_pos > 0.9:
            a_score += 25
        elif price_pos > 0.8:
            a_score += 20
        elif price_pos > 0.6:
            a_score += 10
        # 加速度减速(accel<0)且在上涨中 → 衰竭信号
        if accel < -2 and chg_60d > 10:
            a_score += 15
        elif accel < 0 and chg_60d > 10:
            a_score += 8

        a_score = min(100, a_score)

        return {
            'score': round(a_score, 1),
            'chg_60d': round(chg_60d, 2),
            'chg_250d': round(chg_250d, 2),
            'chg_ratio_60_250': round(chg_ratio, 2),
            'ma20_slope_angle': round(slope_angle, 1),
            'ma20_slope_pct': round(slope_pct, 2),
            'price_pos_60d': round(price_pos, 2),
            'accel_5_10': round(accel, 2),
            'distance_label': self._label_distance(a_score),
        }

    def _dim_b(self, closes: list, volumes: list, highs: list, lows: list,
               n: int, current_chg: float) -> dict:
        """维度B：剩余燃料 — 还能走多远"""
        # 因子⑤ 换手率健康度：从K线间接判断
        # 用量能替代：当前量/20日均量
        if n >= 20:
            vol_ma20 = sum(volumes[:20]) / 20
            vol_ratio = volumes[0] / vol_ma20 if vol_ma20 > 0 else 1.0
        else:
            vol_ma20 = sum(volumes) / n if n > 0 else 1
            vol_ratio = volumes[0] / vol_ma20 if vol_ma20 > 0 else 1.0

        # 因子⑥ 量能趋势：5日均量 vs 20日均量比
        if n >= 20:
            vol_ma5 = sum(volumes[:5]) / 5
            vol_ma20 = sum(volumes[:20]) / 20
            vol_trend = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
        else:
            vol_trend = 1.0

        # 因子⑦ 动量衰减率：后5日/前5日动量
        if n >= 20:
            # 近5日 vs 前5日（隔5日）
            mom_recent = closes[0] - closes[4]  # 涨幅绝对值
            mom_prev = closes[5] - closes[9] if n >= 10 else mom_recent
            mom_decay = mom_recent / mom_prev if mom_prev != 0 else 0
            mom_decay = max(0, min(3, mom_decay))  # 截断到[0,3]
        else:
            mom_decay = 0.5

        # 因子⑧ 持仓成本集中度：用60日均线作为筹码密集区近似
        if n >= 60:
            # 近期价格偏离60日均线的程度（偏离越大说明获利盘越多）
            ma60 = sum(closes[:60]) / 60
            cost_deviation = (closes[0] - ma60) / ma60 * 100 if ma60 > 0 else 0
            # 筹码密集区检测：如果连续20天在±5%区间内震荡
            if n >= 40:
                range_20d = (max(closes[:20]) - min(closes[:20])) / closes[0] * 100
            else:
                range_20d = 20  # 默认宽幅
        else:
            ma60 = sum(closes[:n]) / n if n > 0 else closes[0]
            cost_deviation = 0
            range_20d = 20

        # 维度B评分（值越高说明剩余燃料越足）
        b_score = 0
        # 量比在1.0-2.0之间 = 健康放量 (50分)
        if 0.8 <= vol_ratio <= 2.0:
            b_score += 50
        elif vol_ratio < 0.5:
            b_score += 20  # 缩量
        elif vol_ratio > 3.0:
            b_score += 10  # 爆量=衰竭信号
        else:
            b_score += 30

        # 量能趋势>1 = 放量趋势 (20分)
        b_score += 20 if vol_trend > 1.1 else 10 if vol_trend > 0.9 else 0

        # 动量衰减率>0.8 = 动量还在 (20分)
        b_score += 20 if mom_decay > 0.8 else 10 if mom_decay > 0.5 else 0

        # 成本偏离<20% = 获利盘还没到极端 (10分)
        b_score += 10 if abs(cost_deviation) < 20 else 5 if abs(cost_deviation) < 40 else 0

        b_score = min(100, b_score)

        return {
            'score': round(b_score, 1),
            'vol_ratio': round(vol_ratio, 2),
            'vol_trend_5_20': round(vol_trend, 2),
            'mom_decay': round(mom_decay, 2),
            'cost_deviation_pct': round(cost_deviation, 2),
            'range_20d_pct': round(range_20d, 2),
            'fuel_label': self._label_fuel(b_score),
        }

    def _dim_c(self, closes: list, volumes: list, opens: list,
               highs: list, lows: list, n: int) -> dict:
        """维度C：脱轨信号 — 什么信号说明趋势要断了"""
        warnings = []
        danger_level = 0  # 0=安全, 1=预警, 2=危险, 3=脱轨

        # 因子⑨ 量价背离检测
        if n >= 10:
            chg_5d = (closes[0] - closes[4]) / closes[4] * 100 if closes[4] > 0 else 0
            vol_ma5 = sum(volumes[:5]) / 5
            vol_ma10 = sum(volumes[:10]) / 10
            vol_ratio_vs_10 = vol_ma5 / vol_ma10 if vol_ma10 > 0 else 1.0
            # 价涨量缩背离：涨幅>3%但量比<0.8
            if chg_5d > 3 and vol_ratio_vs_10 < 0.8:
                warnings.append('🔴 量价背离: 价涨量缩')
                danger_level = max(danger_level, 1)
            # 价跌量增背离
            if chg_5d < -3 and vol_ratio_vs_10 > 1.5:
                warnings.append('🔴 量价背离: 量增价跌')
                danger_level = max(danger_level, 1)
        else:
            chg_5d = 0

        # 因子⑩ 趋势线破位
        if n >= 20:
            ma20 = sum(closes[:20]) / 20
            if closes[0] < ma20:
                warnings.append('🟡 跌破20日线')
                danger_level = max(danger_level, 1)
            # 20日线拐头
            if n >= 40:
                ma20_prev = sum(closes[20:40]) / 20
                if ma20 < ma20_prev:
                    warnings.append('🟡 20日线拐头向下')
                    danger_level = max(danger_level, 2)
        if n >= 60:
            ma60 = sum(closes[:60]) / 60
            if closes[0] < ma60:
                warnings.append('🔴 跌破60日线')
                danger_level = max(danger_level, 3)

        # 因子⑪ 加速赶顶
        if n >= 3:
            chg_3d = (closes[0] - closes[2]) / closes[2] * 100 if closes[2] > 0 else 0
        else:
            chg_3d = 0
        # RSI(14)计算
        if n >= 14:
            gains, losses = 0.0, 0.0
            for i in range(14):
                change = closes[i] - closes[i+1] if i+1 < n else 0
                if change > 0: gains += change
                else: losses += abs(change)
            rsi_14 = 100 - (100 / (1 + gains / losses)) if losses > 0 else 100
        else:
            rsi_14 = 50

        if chg_3d > 10 and rsi_14 > 80:
            warnings.append('🔴 加速赶顶: 3日+10%且RSI>80')
            danger_level = max(danger_level, 3)
        elif chg_3d > 7 and rsi_14 > 75:
            warnings.append('🟡 快速拉升: 3日+7%且RSI>75')
            danger_level = max(danger_level, 2)

        # 因子⑫ 资金背离（用K线量价模拟）
        # 盘中资金需要通过实时API获取，这里用K线量能异常判断
        if n >= 5:
            vol_spike = volumes[0] / (sum(volumes[1:6]) / 5) if n >= 6 else 1.0
            if vol_spike > 3.0 and chg_5d > 0:
                warnings.append('🟡 放量滞涨: 量>3倍均量但涨幅有限')
                danger_level = max(danger_level, 2)

        # 综合安全评分（越高越安全）
        safety_map = {0: 100, 1: 60, 2: 30, 3: 0}
        safety_score = safety_map.get(danger_level, 50)

        return {
            'danger_level': danger_level,
            'safety_score': safety_score,
            'warnings': warnings,
            'rsi_14': round(rsi_14, 1),
            'chg_3d': round(chg_3d, 2),
            'ma20_current': round(ma20, 2) if n >= 20 else 0,
            'close_vs_ma20': round(closes[0] / ma20 * 100 - 100, 2) if n >= 20 and ma20 > 0 else 0,
            'close_vs_ma60': round(closes[0] / ma60 * 100 - 100, 2) if n >= 60 and ma60 > 0 else 0,
            'vol_spike': round(vol_spike, 2) if n >= 5 else 0,
            'signal_label': self._label_signal(danger_level),
        }

    def _rate_trend(self, inertia_score: float, dim_c: dict, dim_a: dict = None, dim_b: dict = None) -> tuple:
        """趋势评级——基于三维组合形态，非简单总分"""
        # 先看脱轨信号
        if dim_c['danger_level'] >= 3:
            return '❌ 趋势断裂', '🔴 脱轨预警'
        if dim_c['danger_level'] >= 2:
            return '⚠️ 趋势预警', '🟡 拐点临近'

        da_score = dim_a.get('score', 50) if dim_a else 50
        db_score = dim_b.get('score', 50) if dim_b else 50
        chg_60d = dim_a.get('chg_60d', 0) if dim_a else 0
        accel = dim_a.get('accel_5_10', 0) if dim_a else 0

        # 模式1：末期冲刺 — 60日涨幅巨大 + 价格高位（不管加速度如何）
        if chg_60d > 50 and da_score >= 70:
            return '🔥 末期冲刺', '💥 加速赶顶阶段'
        # 模式1b：涨幅大+价格高位+加速度减速(衰竭中)
        if chg_60d > 15 and da_score >= 50 and (accel < -2 or dim_c.get('rsi_14', 50) > 80):
            return '🔥 末期冲刺', '💥 加速赶顶阶段'

        # 模式2：主升加速 — 涨幅中上+燃料足+无脱轨
        if chg_60d > 15 and db_score >= 60:
            return '⚡ 主升加速', '🚀 健康上行'

        # 模式3：趋势延续 — 小幅上涨或横盘+燃料正常
        if chg_60d > 0 and db_score >= 40:
            return '➡️ 趋势延续', '🟢 惯性尚存'

        # 模式4：蓄力/横盘 — 微跌或横盘
        if db_score >= 30:
            return '🌀 蓄力阶段', '🔵 横盘/筑底'

        # 模式5：趋势衰竭 — 燃料不足+走势弱
        return '❄️ 趋势衰竭', '🟤 动能耗尽'

    def _suggest_action(self, trend_rating: str, dim_c: dict, current_chg: float) -> str:
        """根据评级生成操作建议"""
        if dim_c['danger_level'] >= 3:
            return '🛑 清仓: 趋势已断，空仓等待'
        if dim_c['danger_level'] == 2:
            return '⚠️ 减仓: 拐点信号出现，减至半仓以下'

        if '末期冲刺' in trend_rating:
            return '🎯 分批止盈: 涨了太远，每涨5%减1/3'
        if '主升加速' in trend_rating:
            return '✅ 持有: 趋势健康，不折腾'
        if '趋势延续' in trend_rating:
            return '🟢 可低吸: 趋势尚未走完，回调加仓'
        if '蓄力阶段' in trend_rating:
            return '👀 观望: 等放量突破信号'

        return '📋 等待'

    def _label_distance(self, score: float) -> str:
        if score >= 70: return '🏁 终点附近'
        if score >= 50: return '🏃 过半程'
        if score >= 30: return '🚶 中途'
        return '🏁 刚起步'

    def _label_fuel(self, score: float) -> str:
        if score >= 70: return '⛽ 燃料充足'
        if score >= 50: return '⛽ 燃料过半'
        if score >= 30: return '⚠️ 油量偏低'
        return '🪫 油量告急'

    def _label_signal(self, level: int) -> str:
        return {0: '✅ 无异常', 1: '🟡 轻度预警', 2: '🔶 中度预警', 3: '🔴 严重预警'}.get(level, '✅ 正常')


# ============================================================
# 报告生成
# ============================================================

def generate_report(result: dict) -> str:
    """生成微信推送格式报告"""
    if 'error' in result:
        return f"❌ {result.get('code', '')}: {result['error']}"

    name = result.get('name', result['code'])
    lines = []

    # 头部
    lines.append(f"⚡ **惯性面扫描 — {name} ({result['code']})**")
    lines.append(f"   ⏰ {result['timestamp']}  |  现价: {result['price']}")
    lines.append(f"   当日涨幅: {result.get('change_pct', 0):+.2f}%  |  最新日: {result['latest_date']}")
    lines.append("")

    # 趋势评级
    lines.append(f"   📊 **趋势评级: {result['trend_rating']}**")
    lines.append(f"   惯性评分: {result['inertia_score']}分/100")
    lines.append("")

    # 维度A：已走里程
    da = result['dim_a_distance']
    lines.append(f"   ┌─ **维度A: 已走里程 — {da['distance_label']}**")
    lines.append(f"   │  60日涨幅: {da['chg_60d']:+.2f}%  |  250日涨幅: {da.get('chg_250d', 0):+.2f}%")
    lines.append(f"   │  20日斜率: {da['ma20_slope_angle']}°  |  价格分位: {da['price_pos_60d']*100:.0f}%")
    lines.append(f"   │  动量加速度: {da['accel_5_10']:+.2f}%  |  得分: {da['score']}")
    lines.append("")

    # 维度B：剩余燃料
    db = result['dim_b_fuel']
    lines.append(f"   ├─ **维度B: 剩余燃料 — {db['fuel_label']}**")
    lines.append(f"   │  量比: {db['vol_ratio']:.2f}  |  量能趋势(5/20): {db['vol_trend_5_20']:.2f}")
    lines.append(f"   │  动量衰减: {db['mom_decay']:.2f}  |  成本偏离: {db['cost_deviation_pct']:.1f}%")
    lines.append(f"   │  20日振幅: {db['range_20d_pct']:.1f}%  |  得分: {db['score']}")
    lines.append("")

    # 维度C：脱轨信号
    dc = result['dim_c_signal']
    lines.append(f"   └─ **维度C: 脱轨信号 — {dc['signal_label']}**")
    lines.append(f"      RSI(14): {dc['rsi_14']}  |  3日涨幅: {dc['chg_3d']:+.2f}%")
    lines.append(f"      距20日线: {dc.get('close_vs_ma20', 0):+.1f}%  |  距60日线: {dc.get('close_vs_ma60', 0):+.1f}%")
    if dc['warnings']:
        lines.append(f"      预警:")
        for w in dc['warnings']:
            lines.append(f"        {w}")
    lines.append("")

    # 操作建议
    lines.append(f"   🎯 **操作建议: {result['action']}**")
    lines.append("")
    lines.append(f"   {'─'*40}")
    lines.append("   💡 惯性面核心原则：主升期持有不T，末期逐步减仓")

    return '\n'.join(lines)


def generate_compact_report(results: list) -> str:
    """批量生成精简版报告"""
    lines = []
    lines.append("⚡ **惯性面批量扫描**")
    lines.append(f"   ⏰ {datetime.now().strftime('%H:%M:%S')}")
    lines.append("")

    for r in results:
        if 'error' in r:
            continue
        name = r.get('name', r['code'])
        rating = r['trend_rating']
        score = r['inertia_score']
        action = r['action']
        warnings = r['dim_c_signal']['warnings']
        warn = f" ⚠{len(warnings)}预警" if warnings else ""
        lines.append(f"   {name} | {rating} | {score}分 | {action}{warn}")

    return '\n'.join(lines)


# ============================================================
# 命令行入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description='⚡ 惯性面量化模型')
    parser.add_argument('--code', type=str, help='个股代码，如603986')
    parser.add_argument('--sector', type=str, help='板块/产业链分析')
    parser.add_argument('--scan', action='store_true', help='全量扫描近期强势股')
    parser.add_argument('--watch', action='store_true', help='持续监控模式')
    parser.add_argument('--json', action='store_true', help='输出JSON格式')
    args = parser.parse_args()

    engine = MomentumEngine()

    if args.code:
        rt = fetch_realtime(args.code)
        klines = engine.loader.load_stock(args.code, 250)
        result = engine.analyze(args.code, klines, rt)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(generate_report(result))

    elif args.scan:
        # 从数据库中扫描近期大涨标的做惯性分析
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # 近60日涨幅>30%且成交活跃的标的
        sql = """
        WITH latest AS (
            SELECT code, date, close,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
            FROM kline
        ),
        sixty_ago AS (
            SELECT code, date, close FROM kline WHERE date = (SELECT MIN(date) FROM kline WHERE date >= date('now', '-90 days'))
        )
        SELECT DISTINCT k.code, k.close
        FROM kline k
        WHERE k.date = (SELECT MAX(date) FROM kline)
        ORDER BY k.close ASC
        LIMIT 20
        """
        # 简化方法：直接查近期换手率高的标的
        print("⚡ 全量扫描模式 - 直接使用 --code 指定个股")
        print("   或通过 --sector 板块代码扫描板块内标的")
        conn.close()

    elif args.sector:
        # 板块扫描
        print(f"⚡ 板块惯性扫描: {args.sector}")
        print("   功能建设中，暂用个股模式")
        print("   python3 scripts/momentum.py --code 603986")

    else:
        # 默认：扫描几个核心标的
        codes = ['603986', '601138', '603019', '002281', '603893']
        results = []
        for code in codes:
            rt = fetch_realtime(code)
            klines = engine.loader.load_stock(code, 250)
            if klines:
                result = engine.analyze(code, klines, rt)
                results.append(result)
        print(generate_compact_report(results))
        print()
        for r in results:
            print(generate_report(r))
            print()


if __name__ == '__main__':
    main()
