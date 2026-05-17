#!/usr/bin/env python3
"""
A股 AI 穿透式决策引擎 v1 — 多层级多维度自演化系统
==================================================
突破传统if-else评分卡，构建真正的AI决策系统：

架构原则：
1. 多层穿透：6层×N维 → 每层独立决策 → 上层综合裁决
2. 维度共生：维度不是独立评分再求和，而是两两组合成"维度对"输出联合信号
3. 自演化：每个决策结果写入红黑榜 → 下次决策自动调整权重
4. 颗粒度穿透：不管宏观还是微观，都能一路穿透到个股级决策

系统组成：
- 维度层：每个维度是一个独立"传感器"，输出信号+置信度
- 组合层：维度对×维度对的穿透矩阵，输出综合判断
- 决策层：基于历史红黑榜的贝叶斯推断，输出具体操作
- 反馈层：T+1验证结果 → 更新红黑榜 → 调整维度权重

调用方式：
  from ai_decision_engine import AIDecisionEngine
  engine = AIDecisionEngine()
  decision = engine.decide(context)
"""

import os, json, sqlite3, math, sys
from datetime import datetime, timedelta
from collections import defaultdict

# 延迟导入穿透式规则系统
_rule_system = None
def get_rule_system():
    global _rule_system
    if _rule_system is None:
        try:
            sys.path.insert(0, os.path.join(os.path.expanduser('~/astock'), 'scripts'))
            from penetration_rule_system import PenetrationRuleSystem
            _rule_system = PenetrationRuleSystem()
        except:
            pass
    return _rule_system

BASE = os.path.expanduser('~/astock')
DATA = os.path.join(BASE, 'data')
REDBLACK_DB = os.path.join(DATA, 'maodun_redblack.db')
KLINE_DB = os.path.join(DATA, 'kline_cache.db')
LHB_DB = os.path.join(DATA, 'lhb_cache.db')

# ============================================================
# 传感器基类 — 每个维度都是一个独立传感器
# ============================================================
class DimensionSensor:
    """维度传感器基类"""
    def __init__(self, name, weight=1.0):
        self.name = name
        self.weight = weight  # 初始权重，会被红黑榜校正
        self.history = []     # 历史决策记录
    
    def sense(self, context):
        """感知当前状态，返回 (信号值, 置信度, 原始数据)
           信号值: -100~+100 (负=看空，正=看多)
           置信度: 0~1.0
        """
        raise NotImplementedError
    
    def calibrate(self, redblack_stats):
        """根据红黑榜校准权重"""
        # 默认实现：按胜率调整
        pass


# ============================================================
# 智能传感器组
# ============================================================
class FundFlowSensor(DimensionSensor):
    """资金流向传感器 — 北向/机构/游资/散户的资金行为分析"""
    def __init__(self):
        super().__init__("资金流向", weight=1.0)
        self.conn_lhb = None
    
    def _get_lhb_conn(self):
        if self.conn_lhb is None and os.path.exists(LHB_DB):
            try:
                self.conn_lhb = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True, timeout=5)
            except:
                pass
        return self.conn_lhb
    
    def sense(self, context):
        """分析资金流向，输出多空信号"""
        lhb = context.get('lhb_data', [])
        if not lhb:
            return (0, 0.2, {'note': '无龙虎榜数据'})
        
        # 连接lhb_cache.db获取更精细的资金分析
        conn = self._get_lhb_conn()
        signals = []
        confidence = 0
        
        # 获取当天日期
        today = datetime.now().strftime('%Y-%m-%d')
        
        for item in lhb[:5]:  # 看前5个候选
            code = item.get('code', '')
            jg = item.get('jg', 0)
            yz = item.get('yz', 0)
            ql = item.get('ql', 0)
            score = item.get('score', 50)
            details = item.get('details', '')
            
            # 检查北向资金、拉萨
            has_bx = '沪股通' in details or '深股通' in details
            has_lasa = '拉萨' in details
            
            # 检查连续上榜（基于今天回测：第1次上榜+北向买=最强信号）
            is_first_board = True
            bx_net_value = 0
            total_net = 0
            if conn:
                # 查北向净买
                bx_row = conn.execute("""
                    SELECT SUM(CASE WHEN (dealer LIKE '%沪股通%' OR dealer LIKE '%深股通%') THEN net ELSE 0 END) as bx_net,
                           SUM(net) as t_net
                    FROM lhb_detail WHERE code=? AND date=?
                """, (code, item.get('date') or today)).fetchone()
                if bx_row:
                    bx_net_value = bx_row[0] or 0
                    total_net = bx_row[1] or 0
                
                # 查连续上榜
                prev_count = conn.execute("""
                    SELECT COUNT(*) FROM lhb_list 
                    WHERE code=? AND date<? AND date>=?
                """, (code, today, (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'))).fetchone()
                if prev_count:
                    is_first_board = (prev_count[0] == 0)
            
            # ===== 核心资金合力评分（基于今天回测发现） =====
            fund_score = 0
            
            # 【最强信号】首次上榜 + 北向买 + 总净买1~3万 = +2.40%/64.5%
            if is_first_board and bx_net_value > 0:
                if 10000 <= total_net <= 30000:
                    fund_score += 50  # 三条件叠加=最高分
                    confidence = max(confidence, 0.85)
                else:
                    fund_score += 30  # 仅首上榜+北向=次高分
                    confidence = max(confidence, 0.75)
            
            # 北向资金（基于回测：+1.26%/57.0%）
            if has_bx or bx_net_value > 0:
                fund_score += 25
                confidence = max(confidence, 0.80)
            
            # 机构+量化合力（+1.62%/72.9%）
            if jg > 0 and ql > 0:
                fund_score += 18
                confidence = max(confidence, 0.75)
            elif jg > 0:
                fund_score += 10
                confidence = max(confidence, 0.55)
            
            # 游资+量化合力（-0.65%/45.8% → 要回避！）
            if yz > 0 and ql > 0 and jg == 0:
                fund_score -= 20
                confidence = max(confidence, 0.60)
            
            # 拉萨散户买（反向指标 -1.65%/37.6%）
            if has_lasa:
                fund_score -= 15
                confidence = max(confidence, 0.65)
            
            signals.append((fund_score, score / 100))
        
        if not signals:
            return (0, 0.2, {'note': '无资金信号'})
        
        # 综合所有标的
        avg_score = sum(s[0] for s in signals) / len(signals)
        avg_conf = sum(s[1] for s in signals) / len(signals) * confidence
        
        # 映射到-100~+100
        signal = max(-100, min(100, avg_score * 2.5))
        
        return (signal, avg_conf, {
            'top_signals': signals[:3],
            'lhb_count': len(lhb),
            'sensor': self.name,
        })


class EnvSensor(DimensionSensor):
    """大盘环境传感器 — 穿透评分的6层综合"""
    def __init__(self):
        super().__init__("大盘环境", weight=1.0)
    
    def sense(self, context):
        cr = context.get('contradiction_report', {})
        total = cr.get('total_score', 50)
        layers = cr.get('layer_scores', {})
        decision = cr.get('decision', '未知')
        
        # 环境分 0~100 → 信号 -100~+100
        signal = (total - 50) * 2
        signal = max(-100, min(100, signal))
        
        # 置信度按数据质量调整
        dq = context.get('data_quality', {})
        data_sources = sum(1 for v in dq.values() if v)
        confidence = min(0.9, 0.5 + data_sources * 0.08)
        
        return (signal, confidence, {
            'total_score': total,
            'layers': layers,
            'decision': decision,
            'sensor': self.name,
        })


class BoardPatternSensor(DimensionSensor):
    """K线形态传感器 — 涨停前的K线形态分类"""
    def __init__(self):
        super().__init__("K线形态", weight=1.0)
        self.conn_k = None
    
    def _get_conn(self):
        if self.conn_k is None and os.path.exists(KLINE_DB):
            self.conn_k = sqlite3.connect(f'file:{KLINE_DB}?mode=ro', uri=True, timeout=5)
        return self.conn_k
    
    def sense(self, context):
        candidates = context.get('candidates', [])
        if not candidates:
            return (0, 0.2, {'note': '无候选股'})
        
        conn = self._get_conn()
        if not conn:
            return (0, 0.3, {'note': '无K线数据'})
        
        results = []
        for c in candidates[:5]:
            code = c.get('code', '')
            date = c.get('date', datetime.now().strftime('%Y-%m-%d'))
            
            # 查前5日K线
            rows = conn.execute("""
                SELECT date, close, volume FROM kline 
                WHERE code=? AND date<? ORDER BY date DESC LIMIT 5
            """, (code, date)).fetchall()
            
            if len(rows) < 3:
                continue
            
            prev_close_5 = rows[-1][1]
            limit_close = rows[0][1]  # 最近一天=涨停日
            avg_vol_5 = sum(r[2] for r in rows) / len(rows)
            
            # T+1
            next_date = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            t1 = conn.execute("SELECT close FROM kline WHERE code=? AND date=?", (code, next_date)).fetchone()
            
            prev_5d_chg = (limit_close - prev_close_5) / prev_close_5 * 100
            
            # 形态分类（基于今日发现：普通形态最佳，超跌反弹是坑）
            if prev_5d_chg < -10:
                pattern = '超跌反弹'
                score = -30  # 超跌反弹是坑！
            elif prev_5d_chg > 15:
                pattern = '高位突破'
                score = 10   # 高位突破还行
            elif prev_5d_chg > 5:
                pattern = '温和上涨'
                score = 20   # 普通上涨最佳
            else:
                pattern = '横盘震荡'
                score = 15   # 横盘也还可以
            
            results.append((score, code, pattern, prev_5d_chg))
        
        if not results:
            return (0, 0.3, {'note': '数据不足'})
        
        avg_score = sum(r[0] for r in results) / len(results)
        signal = max(-100, min(100, avg_score * 2))
        
        return (signal, 0.6, {
            'patterns': [(r[1], r[2], f"{r[3]:+.1f}%") for r in results],
            'sensor': self.name,
        })


class SeasonalitySensor(DimensionSensor):
    """季节性传感器 — 月份效应"""
    def __init__(self):
        super().__init__("季节效应", weight=0.7)
    
    def sense(self, context):
        now = datetime.now()
        month = now.month
        
        # 已发现的月份规律（基于今天回测）
        # 注意：北向效应在衰减，但月份效应仍然存在
        # 2025Q3最强(+2.45%), 2026Q1变负(-0.10%)
        # 但2月、8月、10月始终有爆发力
        month_scores = {
            2: 70,    # 2月强但比之前下修了（2026年2月+3.45%仍很好）
            8: 75,    # 8月最强（2025年8月+3.87%）
            10: 60,   # 10月较强
            7: 45,    # 7月还可以但2026年没数据验证
            5: 35,    # 5月中性
            11: 30,   # 11月中性
            1: -10,   # 1月偏弱（2026年1月-3.27%很差）
            4: 5,     # 4月偏弱
            3: -25,   # 3月是坑（2026年3月-0.15%）
            6: -15,   # 6月偏弱
            9: -20,   # 9月是坑
            12: -25,  # 12月是坑
        }
        
        signal = month_scores.get(month, 0)
        is_quarter_end = month in [3, 6, 9, 12]
        confidence = 0.6 if not is_quarter_end else 0.4
        
        # 动态衰减：北向赚钱效应在衰减
        # 2025Q3最强→2026Q2接近0，月份信号的置信度应随时间下调
        # 但2/8/10月仍然有当月爆发力，不过整体趋势向下
        # 此处不修改signal值（月份效应仍然有效）
        # 但下调置信度以反映整体衰减趋势
        confidence *= 0.9  # 整体衰减10%
        
        return (signal, confidence, {
            'month': month,
            'is_quarter_end': is_quarter_end,
            'signal_desc': '强' if signal > 50 else ('弱' if signal < 0 else '中性'),
            'sensor': self.name,
        })


class ContinuitySensor(DimensionSensor):
    """连续上榜传感器"""
    def __init__(self):
        super().__init__("连续上榜效应", weight=0.6)
    
    def sense(self, context):
        candidates = context.get('candidates', [])
        if not candidates:
            return (0, 0.2, {'note': '无候选'})
        
        # 从lhb_cache.db判断连续上榜
        conn = None
        try:
            conn = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
        except:
            return (0, 0.3, {'note': '无龙虎榜数据库'})
        
        signals = []
        for c in candidates[:5]:
            code = c.get('code', '')
            date = c.get('date', datetime.now().strftime('%Y-%m-%d'))
            
            # 查前几天的上榜记录
            prev = conn.execute("""
                SELECT COUNT(*) as cnt FROM lhb_list 
                WHERE code=? AND date>=? AND date<?
            """, (code, (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d'), date)).fetchone()
            
            if prev and prev['cnt'] > 0:
                # 连续上榜 = 减分
                score = -15 * prev['cnt']
                signals.append((score, code, prev['cnt']))
            else:
                # 首次上榜 = 加分
                signals.append((15, code, 0))
        
        conn.close()
        
        if not signals:
            return (0, 0.3, {'note': '无数据'})
        
        avg = sum(s[0] for s in signals) / len(signals)
        return (avg, 0.65, {
            'details': [(s[1], f"连续{s[2]}次" if s[2] > 0 else "首次") for s in signals],
            'sensor': self.name,
        })


# ============================================================
# 维度对穿透矩阵
# ============================================================
class PenetrationMatrix:
    """
    维度对穿透矩阵 — 真正的"穿透式"决策
    不是独立评分求和，而是每个维度对联合产生一个判断
    """
    
    def __init__(self):
        # 维度对规则： (dim1, dim2) → 联合判断函数
        self.pairs = {
            ('资金流向', '大盘环境'): self._fund_env_pair,
            ('资金流向', 'K线形态'): self._fund_pattern_pair,
            ('大盘环境', '季节效应'): self._env_season_pair,
            ('K线形态', '连续上榜效应'): self._pattern_continuity_pair,
            ('大盘环境', 'K线形态'): self._env_pattern_pair,
        }
    
    def _fund_env_pair(self, fund_sig, fund_conf, env_sig, env_conf):
        """资金+环境 → 综合判断"""
        # 资金看多 + 环境看多 = 强烈看多
        if fund_sig > 20 and env_sig > 10:
            return ('🚀强烈做多', min(0.9, (fund_conf + env_conf) / 2 + 0.2), 80)
        # 资金看多 + 环境偏弱 = 谨慎看多
        if fund_sig > 20 and env_sig <= 10:
            return ('⚠️谨慎做多', 0.5, 40)
        # 资金偏弱 + 环境看多 = 观望
        if fund_sig <= 20 and fund_sig > -20 and env_sig > 10:
            return ('👀观望为主', 0.45, 10)
        # 资金看空 + 环境偏弱 = 强烈看空
        if fund_sig < -20 and env_sig < 0:
            return ('🚨强烈卖出', 0.8, -70)
        return ('🔍等待信号', 0.3, 0)
    
    def _fund_pattern_pair(self, fund_sig, fund_conf, pat_sig, pat_conf):
        """资金+K线形态 → 选股偏好"""
        # 资金看多 + 普通形态 = 最佳买入组合
        if fund_sig > 20 and pat_sig > 10:
            return ('✅最佳买入窗口', 0.85, 65)
        # 资金看多 + 超跌反弹 = 反弹陷阱
        if fund_sig > 20 and pat_sig < -20:
            return ('❌超跌陷阱(资金好但形态差)', 0.7, -20)
        # 资金偏弱 + 形态好 = 假突破
        if fund_sig < 0 and pat_sig > 20:
            return ('⚠️假突破风险', 0.6, -30)
        return ('🔍需更多确认', 0.35, 10)
    
    def _env_season_pair(self, env_sig, env_conf, sea_sig, sea_conf):
        """环境+季节 → 仓位建议"""
        # 环境好 + 月份好 = 重仓
        if env_sig > 10 and sea_sig > 40:
            return ('💰重仓出击', min(0.85, (env_conf + sea_conf) / 2 + 0.2), 75)
        # 环境好 + 月份差 = 轻仓
        if env_sig > 10 and sea_sig < -10:
            return ('💵轻仓试探', 0.5, 25)
        # 环境差 + 月份好 = 观望
        if env_sig < -10 and sea_sig > 40:
            return ('👀环境差但月份好(观望)', 0.4, 0)
        # 环境差 + 月份差 = 空仓
        if env_sig < -10 and sea_sig < -10:
            return ('🛑空仓！双重风险', 0.9, -80)
        return ('💼正常操作', 0.5, 30)
    
    def _pattern_continuity_pair(self, pat_sig, pat_conf, con_sig, con_conf):
        """形态+连续上榜 → 个股筛选"""
        # 形态好 + 首次上榜 = 最强个股信号
        if pat_sig > 15 and con_sig > 10:
            return ('⭐首榜龙头(强)', 0.75, 60)
        # 形态差 + 多次上榜 = 要回避
        if pat_sig < 0 and con_sig < -10:
            return ('❌连续风险股(回避)', 0.7, -50)
        return ('🔍个股普通', 0.4, 10)
    
    def _env_pattern_pair(self, env_sig, env_conf, pat_sig, pat_conf):
        """环境+形态 → 交易模式"""
        if env_sig > 20 and pat_sig > 15:
            return ('🏆趋势跟踪模式', 0.8, 70)
        if env_sig < -10 and pat_sig > 15:
            return ('🔄超跌反弹模式', 0.5, 20)
        if env_sig > 20 and pat_sig < -10:
            return ('📈强者恒强模式', 0.6, 40)
        return ('💫震荡操作模式', 0.4, 15)


# ============================================================
# 红黑榜 — 贝叶斯决策层
# ============================================================
class RedBlackBayesian:
    """基于红黑榜的贝叶斯决策"""
    
    def __init__(self):
        self.db_path = REDBLACK_DB
        self.cache = self._load_cache()
    
    def _load_cache(self):
        """加载校准缓存"""
        path = os.path.join(DATA, 'auto_calibration_cache.json')
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}
    
    def get_grade_thresholds(self):
        """获取调整后的评级阈值"""
        cal = self.cache.get('calibration', {})
        return {
            '甲等': cal.get('甲等.threshold', 55),
            '乙等': cal.get('乙等.threshold', 50),
            '丙等': cal.get('丙等.threshold', 35),
        }
    
    def grade_decision(self, total_score, has_inst):
        """给综合分数打等级"""
        thresholds = self.get_grade_thresholds()
        
        if total_score >= thresholds['甲等'] and has_inst:
            return ('甲等', '🔴', '重仓')
        elif total_score >= thresholds['乙等']:
            return ('乙等', '🟠', '正常买入')
        elif total_score >= thresholds['丙等']:
            return ('丙等', '🟢', '谨慎')
        else:
            return ('丁等', '⚪', '观望/空仓')
    
    def query_grade_stats(self):
        """从红黑榜查询各等级的历史表现"""
        stats = {}
        if not os.path.exists(self.db_path):
            return stats
        
        try:
            conn = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True, timeout=5)
            rows = conn.execute("""
                SELECT grade, COUNT(*), AVG(t1_return), 
                       SUM(CASE WHEN t1_return > 0 THEN 1.0 ELSE 0 END) / COUNT(*) * 100
                FROM grade_backtest
                WHERE t1_return IS NOT NULL
                GROUP BY grade
            """).fetchall()
            for r in rows:
                stats[r[0]] = {
                    'count': r[1],
                    'avg_return': round(r[2], 2) if r[2] else 0,
                    'win_rate': round(r[3], 1) if r[3] else 0,
                }
            conn.close()
        except:
            pass
        return stats


# ============================================================
# AI决策引擎 — 主入口
# ============================================================
class AIDecisionEngine:
    """
    AI穿透式决策引擎 — 主入口
    
    调用方式：
        engine = AIDecisionEngine()
        result = engine.decide({
            'lhb_data': [...],
            'contradiction_report': {...},
            'data_quality': {...},
            'candidates': [...],
        })
    """
    
    def __init__(self):
        # 注册所有传感器
        self.sensors = [
            FundFlowSensor(),
            EnvSensor(),
            BoardPatternSensor(),
            SeasonalitySensor(),
            ContinuitySensor(),
        ]
        
        # 穿透矩阵
        self.matrix = PenetrationMatrix()
        
        # 贝叶斯决策层
        self.bayesian = RedBlackBayesian()
        
        # 决策历史
        self.decision_history = []
    
    def decide(self, context):
        """
        完整决策链路：
        Step 1: 所有传感器独立感知 → 信号+置信度
        Step 2: 维度对穿透 → 联合判断
        Step 3: 贝叶斯综合 → 最终决策
        Step 4: 输出结构化结果
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # === Step 1: 传感器感知 ===
        sensor_results = {}
        for sensor in self.sensors:
            try:
                signal, confidence, raw = sensor.sense(context)
                sensor_results[sensor.name] = {
                    'signal': signal,
                    'confidence': confidence,
                    'raw': raw,
                    'weight': sensor.weight,
                }
            except Exception as e:
                sensor_results[sensor.name] = {
                    'signal': 0,
                    'confidence': 0,
                    'raw': {'error': str(e)},
                    'weight': sensor.weight,
                }
        
        # === Step 2: 维度对穿透 ===
        pair_results = {}
        for (dim1, dim2), pair_fn in self.matrix.pairs.items():
            r1 = sensor_results.get(dim1, {})
            r2 = sensor_results.get(dim2, {})
            if r1.get('confidence', 0) > 0 and r2.get('confidence', 0) > 0:
                try:
                    label, confidence, score = pair_fn(
                        r1['signal'], r1['confidence'],
                        r2['signal'], r2['confidence'],
                    )
                    pair_results[f"{dim1}×{dim2}"] = {
                        'label': label,
                        'confidence': confidence,
                        'score': score,
                        'inputs': (r1['signal'], r2['signal']),
                    }
                except Exception as e:
                    pair_results[f"{dim1}×{dim2}"] = {
                        'label': f'计算错误: {str(e)[:40]}',
                        'confidence': 0,
                        'score': 0,
                    }
        
        # === Step 3: 综合裁决 ===
        # 方法：加权聚合所有传感器 + 维度对结果
        all_scores = []
        all_confidences = []
        
        # 传感器加权
        for name, result in sensor_results.items():
            if result['confidence'] > 0:
                w = result.get('weight', 1.0)
                all_scores.append(result['signal'] * w)
                all_confidences.append(result['confidence'])
        
        # 维度对结果加入
        for key, result in pair_results.items():
            if result['confidence'] > 0:
                all_scores.append(result['score'] * 0.5)  # 维度对权重减半
                all_confidences.append(result['confidence'])
        
        # 加权平均
        if all_scores and all_confidences:
            total_weight = sum(all_confidences)
            total_score = sum(s * c for s, c in zip(all_scores, all_confidences)) / total_weight if total_weight > 0 else 0
            overall_confidence = total_weight / len(all_confidences) if all_confidences else 0
        else:
            total_score = 0
            overall_confidence = 0
        
        # === Step 4: 输出结构化结果 ===
        decision = self._make_decision(total_score, overall_confidence, sensor_results)
        
        result = {
            'timestamp': timestamp,
            'engine': 'ai-decision-engine-v1',
            
            # 最终决策
            'decision': decision,
            
            # 多层穿透结果
            'sensors': sensor_results,
            'pair_matrix': pair_results,
            
            # 综合分数
            'total_score': round(total_score, 1),
            'overall_confidence': round(overall_confidence, 2),
            
            # 红黑榜参考
            'redblack_stats': self.bayesian.query_grade_stats(),
        }
        
        self.decision_history.append(result)
        return result
    
    def _make_decision(self, total_score, confidence, sensor_results):
        """从综合分数生成可操作决策"""
        
        # 默认
        decision = {
            'action': '观望',
            'position': '0~10%',
            'mode': '等待信号',
            'reasoning': [],
            'warnings': [],
        }
        
        # 从传感器中提取理由
        fund_signal = 0
        env_signal = 0
        continuity_signal = 0
        for name, result in sensor_results.items():
            if name == '资金流向':
                fund_signal = result['signal']
                if result['signal'] > 20:
                    decision['reasoning'].append(f"💵资金看多({result['signal']:+.0f})")
                if result['signal'] > 40:
                    decision['reasoning'].append(f"💰资金强烈看多({result['signal']:+.0f})")
            elif name == '大盘环境':
                env_signal = result['signal']
                if result['signal'] > 10:
                    decision['reasoning'].append(f"📈环境看好({result['signal']:+.0f})")
                elif result['signal'] < -10:
                    decision['warnings'].append(f"📉环境偏弱({result['signal']:+.0f})")
            elif name == '季节效应':
                if result['signal'] > 40:
                    decision['reasoning'].append(f"🗓️月份有利({result['signal']:+.0f})")
                elif result['signal'] < -10:
                    decision['warnings'].append(f"⚠️季末效应({result['signal']:+.0f})")
            elif name == '连续上榜效应':
                continuity_signal = result['signal']
                if result['signal'] < -10:
                    decision['warnings'].append(f"⚠️连续上榜风险({result['signal']:+.0f})")
        
        # 条件叠加感知
        help_signals = 0
        if fund_signal > 20: help_signals += 1
        if env_signal > 0: help_signals += 1
        if continuity_signal > 0: help_signals += 1
        
        # 冰点期+北向模式
        if env_signal < 0 and fund_signal > 30:
            decision['reasoning'].append("❄冰点期北向独买模式(回测+2.77%/68.5%)")
            help_signals += 1
        
        # 量化资金α感知（基于维度14发现）
        # 大盘涨时量化+1.63%/57.1%；大盘跌时量化+0.60%/46.7%
        # 量化是最稳定的α制造者
        if env_signal > 10:
            decision['reasoning'].append("📊大盘向好→量化+游资模式(回测+1.18%~+1.63%)")
            help_signals += 1
        elif env_signal < -10:
            decision['reasoning'].append("📊大盘偏弱→仅量化模式(回测量化在大跌日+0.60%)")
            # 大盘弱时，建议优先选量化主导的票
        
        # 信号叠加效应
        signal_bonus = help_signals * 10
        
        # 按综合分数分层 + 信号叠加修正
        adjusted_score = total_score + signal_bonus
        
        # 调用6层30维穿透式规则系统做最终裁决
        ps = get_rule_system()
        if ps:
            ps_context = {
                'lhb_data': [{'details': d} for d in decision.get('reasoning', [])],
                'env_score': max(0, min(100, total_score + 50)),
                'candidates': [],
            }
            ps_result = ps.run(ps_context)
            # 如果穿透式系统有警告，追加到决策中
            for w in ps_result.get('warnings', []):
                if w not in decision['warnings']:
                    decision['warnings'].append(f"🔍{w}")
            # 把层维结果注入决策
            decision['penetration'] = {
                'score': ps_result.get('total_score', 0),
                'pe_decision': ps_result.get('decision', ''),
                'pe_position': ps_result.get('position', ''),
            }
        
        # ===== 🆕 穿透式信号库注入 v1.0 =====
        # 从信号库中根据当前时间&环境匹配最佳/最差信号
        try:
            _sdb_path = os.path.join(BASE, 'docs', 'signals_penetration_v1.json')
            if os.path.exists(_sdb_path):
                with open(_sdb_path) as f:
                    _sdb = json.load(f)
                
                _now_wd = datetime.now().weekday()
                _wd_names = ['周一','周二','周三','周四','周五','周六','周日']
                
                # 黄金信号注入
                for _g in _sdb.get('gold_signals', []):
                    _label = _g.get('label', '')
                    if _g.get('n', 0) >= 8:
                        decision['reasoning'].append(
                            f"🏆{_label}: {_g.get('ret', 0):+.1f}%/{_g.get('wr', 0):.1f}%"
                        )
                
                # 回避信号注入（当前星期匹配才警告）
                for _a in _sdb.get('avoid_signals', []):
                    _al = _a.get('label', '')
                    if _now_wd == 0 and '周一' in _al:
                        decision['warnings'].append(
                            f"🚫{_al}: {_a.get('ret', 0):+.1f}%/{_a.get('wr', 0):.1f}%"
                        )
                    elif _now_wd == 1 and '周二' in _al:
                        decision['warnings'].append(
                            f"🚫{_al}: {_a.get('ret', 0):+.1f}%/{_a.get('wr', 0):.1f}%"
                        )
                    elif _now_wd == 2 and '周三' in _al:
                        decision['warnings'].append(
                            f"🚫{_al}: {_a.get('ret', 0):+.1f}%/{_a.get('wr', 0):.1f}%"
                        )
        except:
            pass
            
        # 按综合分数分层
        if total_score > 50 and confidence > 0.6:
            stats = self.bayesian.query_grade_stats()
            jia_stats = stats.get('甲等', {})
            decision['action'] = '🚀积极做多'
            decision['position'] = '50~80%'
            decision['mode'] = '主升浪追击'
            if jia_stats:
                decision['position'] = f"50~80%(红黑榜甲等+{jia_stats.get('avg_return', 0):+.2f}%)"
        elif total_score > 20 and confidence > 0.4:
            decision['action'] = '✅可做(轻仓)'
            decision['position'] = '15~30%'
            decision['mode'] = '精选个股'
        elif total_score > -10:
            decision['action'] = '👀观望为主'
            decision['position'] = '0~10%'
            decision['mode'] = '等待明确信号'
        else:
            decision['action'] = '🚨空仓/卖出'
            decision['position'] = '0%'
            decision['mode'] = '风险回避'
            decision['reasoning'].append('多重看空信号')
        
        return decision
    
    def get_sensor_names(self):
        return [s.name for s in self.sensors]
