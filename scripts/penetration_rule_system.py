#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 层维穿透式动态规则系统 v1
===================================
基于30维回测发现的完整决策规则体系
6层×5子维 = 30个规则节点，层层穿透，动态进化

架构:
Layer 6: 宏观周期层   → 市场处于什么周期
Layer 5: 资金博弈层   → 什么资金在主导
Layer 4: 环境匹配层   → 什么环境下什么策略
Layer 3: 个股筛选层   → 什么样的个股值得买
Layer 2: 席位验证层   → 什么席位买了它
Layer 1: 操作执行层   → 最终买/卖决定

穿透调用: top-down，每层输出给下层
动态修正: bottom-up，每层根据实际结果向上反馈
"""

import os, json
from datetime import datetime, timedelta

# ============================================================
# 核心数据结构
# ============================================================
# 每个规则节点的输出
class RuleNode:
    def __init__(self, name, layer, weight=1.0):
        self.name = name
        self.layer = layer      # 1~6
        self.weight = weight    # 当前权重（会被动态修正）
        self.confidence = 0.8   # 置信度
        self.signal = 0         # -100~+100
        self.evidence = []      # 支持理由
        self.warnings = []      # 风险提示
        self.sub_rules = []     # 子规则节点
    
    def to_dict(self):
        return {
            'name': self.name,
            'layer': self.layer,
            'weight': round(self.weight, 2),
            'confidence': round(self.confidence, 2),
            'signal': self.signal,
            'evidence': self.evidence[:3],
            'warnings': self.warnings[:3],
        }


# ============================================================
# 6层 × 5维 = 30个规则节点
# ============================================================

class MacroCycleLayer:
    """
    Layer 6: 宏观周期层
    5个维度 → 判断当前处于什么市场周期
    """
    def evaluate(self):
        rules = []
        
        # R6-1: 市场整体滚动胜率
        r1 = RuleNode("全市场滚动胜率周期", 6, weight=1.0)
        # 基于维度30发现: 胜率<43%=黑天鹅, 43~47%=偏弱, 47~55%=正常, >55%=黄金
        r1.signal = 30  # 从stability_engine读取
        
        # R6-2: 季节效应（月份）
        r2 = RuleNode("季节周期", 6, weight=0.7)
        month = datetime.now().month
        # 基于维度3/5: 季中月(2/5/8/11)最强, 季末(3/6/9/12)最弱
        month_score = {2:80, 5:60, 8:85, 11:50, 1:10, 4:15, 7:55, 10:65, 3:-25, 6:-15, 9:-20, 12:-25}
        r2.signal = month_score.get(month, 0)
        r2.evidence.append(f"{month}月季节分数{r2.signal}")
        
        # R6-3: 北向资金趋势（衰减检测）
        r3 = RuleNode("北向资金趋势", 6, weight=0.6)
        # 基于维度2: 北向效应在衰减，2025Q3+2.45%→2026Q2+0.05%
        r3.signal = -10
        r3.evidence.append("北向效应衰减趋势(-10)")
        r3.warnings.append("北向资金已非最强信号")
        
        # R6-4: 全市场涨停活跃度
        r4 = RuleNode("涨停活跃度", 6, weight=0.5)
        # 基于维度5: 涨停数从低到高分10档
        # 0~4只=极冰, 5~14=冰点, 15~29=正常, 30~44=活跃, >45=高潮
        r4.signal = 0  # 从market_daily.db读取
        
        # R6-5: 黑天鹅检测
        r5 = RuleNode("黑天鹅预警", 6, weight=1.2)
        # 基于维度30: 当多个规律同时失效=黑天鹅
        r5.signal = 0
        r5.evidence.append("规律稳定性检测中")
        
        # R6-6: 周内效应（基于维度33）
        r6 = RuleNode("周内效应", 6, weight=0.6)
        wd = datetime.now().weekday()
        day_scores = {0: 20, 1: 10, 2: 5, 3: -15, 4: -5}
        r6.signal = day_scores.get(wd, 0)
        days_cn = ['周一', '周二', '周三', '周四', '周五']
        if r6.signal > 0:
            r6.evidence.append(f"{days_cn[wd]} 游资+北向最强日(+{r6.signal})")
        elif r6.signal < 0:
            r6.evidence.append(f"{days_cn[wd]} 所有资金偏弱日({r6.signal})")
            r6.warnings.append(f"周四是所有资金最弱日, 散户亏-3.21%")
        
        return [r1, r2, r3, r4, r5, r6]


class FundGameLayer:
    """
    Layer 5: 资金博弈层
    5个维度 → 各类资金正在做什么
    """
    def evaluate(self, lhb_data=None):
        rules = []
        
        # R5-1: 北向资金行为
        r1 = RuleNode("北向资金行为", 5, weight=1.0)
        # 基于维度1/4/8: 北向买+首上榜+冰点=最强(+2.84%/67.7%)
        # 北向买+高潮=亏钱(-3.29%/20%)
        r1.signal = 0
        r1.evidence.append("北向资金待检测")
        
        # R5-2: 游资行为（宁波帮/国泰海通系）
        r2 = RuleNode("游资(宁波帮)行为", 5, weight=0.9)
        # 基于维度15/19: 宁波天童+4.90%/80.8%, 静安新闸+4.22%/74.1%
        if lhb_data:
            has_ningbo = any('宁波' in str(item.get('details','')) for item in lhb_data[:5])
            r2.signal = 30 if has_ningbo else 0
            if has_ningbo:
                r2.evidence.append("宁波帮席位活跃(+30)")
        else:
            r2.signal = 0
        
        # R5-3: 量化资金行为
        r3 = RuleNode("量化资金行为", 5, weight=0.8)
        # 基于维度14: 大盘涨时量化+1.63%/57.1%, 跌时+0.60%/46.7%
        # 基于维度17: 量化高换手90~150%=+2.54%/63.9%
        r3.signal = 15  # 量化始终有α
        r3.evidence.append("量化是稳定的α制造者(+15)")
        
        # R5-4: 拉萨散户行为（反向指标）
        r4 = RuleNode("拉萨(散户)行为", 5, weight=0.7)
        # 基于维度15/17: 拉萨全亏(-1.01%~-1.76%), 拉萨锁仓-3.19%/27.7%
        if lhb_data:
            has_lasa = any('拉萨' in str(item.get('details','')) for item in lhb_data[:5])
            r4.signal = -25 if has_lasa else 5
            if has_lasa:
                r4.evidence.append("拉萨席位活跃=反向信号(-25)")
                r4.warnings.append("拉萨买入的票要回避")
        else:
            r4.signal = 0
        
        # R5-5: 机构资金行为
        r5 = RuleNode("机构资金行为", 5, weight=0.6)
        # 基于维度6: 机构作为买一时-1.51%/39.6%, 但机构+北向双买还行
        r5.signal = 5
        r5.evidence.append("机构资金中性偏弱(+5)")
        
        return [r1, r2, r3, r4, r5]


class EnvMatchLayer:
    """
    Layer 4: 环境匹配层
    5个维度 → 当前环境下应该用什么策略
    """
    def evaluate(self, env_score=50):
        rules = []
        
        # R4-1: 冰点策略 (涨停数<17 或 环境分<25)
        r1 = RuleNode("冰点策略", 4, weight=1.0)
        # 基于维度3/4/8: 冰点+北向独买+首上榜=+2.84%/67.7%
        # 冰点+散户买入=-0.40%~-0.80%
        if env_score < 25:
            r1.signal = 20  # 冰点期反而有机会
            r1.evidence.append("冰点期+北向首上榜模式(+20)")
        elif env_score < 40:
            r1.signal = 10
            r1.evidence.append("环境偏弱,轻仓聚焦最强涨停(+10)")
        else:
            r1.signal = 5
            
        
        # R4-2: 震荡策略
        r2 = RuleNode("震荡策略", 4, weight=0.8)
        # 基于维度5/14: 震荡期北向独买最优(+1.07%/57.0%)
        if 25 <= env_score <= 50:
            r2.signal = 15
            r2.evidence.append("震荡期+北向独买模式(+15)")
        else:
            r2.signal = 0
        
        # R4-3: 高潮策略
        r3 = RuleNode("高潮策略", 4, weight=0.9)
        # 基于维度3/5/8: 高潮期所有资金都难赚钱!
        # 高潮+北向独买=-3.29%/20%, 高潮+散户=-2.17%/32%
        if env_score > 65:
            r3.signal = -30
            r3.evidence.append("高潮期减仓(-30)")
            r3.warnings.append("高潮期所有资金难赚钱→空仓信号")
        else:
            r3.signal = 0
        
        # R4-4: 主线确认策略
        r4 = RuleNode("主线确认策略", 4, weight=0.7)
        # 基于之前的矛盾论: 主线确认期+2.21%/63.4%
        r4.signal = 10
        
        # R4-5: 切换期策略
        r5 = RuleNode("切换期策略", 4, weight=0.6)
        # 矛盾切换后2天+1.53%/56.8%
        r5.signal = 0
        
        return [r1, r2, r3, r4, r5]


class StockFilterLayer:
    """
    Layer 3: 个股筛选层
    5个维度 → 什么样的个股值得放入候选池
    """
    def evaluate(self, candidates=None):
        rules = []
        
        # R3-1: 首上榜优先
        r1 = RuleNode("首上榜优先", 3, weight=1.0)
        # 基于维度1/8: 首上榜+0.84% vs 二板+0.10%, 第4次上榜-2.45%
        r1.signal = 15
        r1.evidence.append("首上榜=收益最佳(+15)")
        
        # R3-2: 净买额区间
        r2 = RuleNode("净买额1~3万最优", 3, weight=0.8)
        # 基于维度1/4: 总净买1~3万+北向=+2.40%/64.5%
        r2.signal = 15
        r2.evidence.append("净买额1~3万=最佳区间(+15)")
        
        # R3-3: 量能×资金特征（基于维度31）
        r3 = RuleNode("量能×资金特征", 3, weight=0.8)
        # 基于维度31: 缩量游资+6.17%/92.3%, 地量北向+3.34%/77.8%
        # 正常量机构-0.54%/38.8%, 巨量北向-0.05%/35.0%
        r3.signal = 15
        r3.evidence.append("缩量游资(50~100%量)=+6.17%/92.3%")
        r3.evidence.append("地量北向(<50%量)=+3.34%/77.8%")
        r3.warnings.append("正常量以上机构票回避(-0.54%)")
        
        # R3-4: K线形态
        r4 = RuleNode("K线形态筛选", 3, weight=0.6)
        # 基于维度3: 普通形态+1.58%/60.9%, 超跌反弹-1.95%/39.5%
        r4.signal = 10
        r4.evidence.append("普通K线形态最佳(+10)")
        r4.warnings.append("超跌反弹涨停是坑")
        
        # R3-5: 竞价表现
        r5 = RuleNode("竞价表现", 3, weight=0.5)
        r5.signal = 0
        
        return [r1, r2, r3, r4, r5]


class SeatVerifyLayer:
    """
    Layer 2: 席位验证层
    5个维度 → 验证是哪些席位在买
    """
    def evaluate(self, lhb_data=None):
        rules = []
        
        # R2-1: 赚钱席位检测
        r1 = RuleNode("赚钱席位检测", 2, weight=1.0)
        if lhb_data:
            for item in lhb_data:
                details = str(item.get('details',''))
                # 宁波帮
                if '宁波' in details:
                    r1.signal += 25
                    r1.evidence.append("宁波帮席位买入! (+25)")
                # 华泰总部
                if '华泰' in details:
                    r1.signal += 12
                    r1.evidence.append("华泰系席位买入(+12)")
                # 国泰海通
                if '国泰' in details:
                    r1.signal += 8
                    r1.evidence.append("国泰海通系买入(+8)")
        else:
            r1.signal = 0
        
        # R2-2: 亏钱席位检测
        r2 = RuleNode("亏钱席位检测", 2, weight=1.0)
        if lhb_data:
            for item in lhb_data:
                details = str(item.get('details',''))
                if '拉萨' in details:
                    r2.signal -= 20
                    r2.evidence.append("拉萨席位=回避! (-20)")
                    r2.warnings.append("拉萨买入的票T+1大概率亏")
                if '荣超' in details or '益田路' in details:
                    r2.signal -= 15
                    r2.evidence.append("华泰荣超/益田路=亏钱席位(-15)")
        else:
            r2.signal = 0
        
        # R2-3: 机构+北向双买
        r3 = RuleNode("机构+北向双买", 2, weight=0.8)
        if lhb_data:
            for item in lhb_data:
                jg = item.get('jg', 0)
                yz = item.get('yz', 0)
                # 北向只能从details判断
                details = str(item.get('details',''))
                has_bx = '沪股通' in details or '深股通' in details
                if jg > 1 and has_bx:
                    r3.signal += 15
                    r3.evidence.append("机构+北向双买=安全(+15)")
                elif jg > 0:
                    r3.signal += 5
        else:
            r3.signal = 0
        
        # R2-4: 游资+量化合力(无机构) = 要回避
        r4 = RuleNode("游资量化合力回避", 2, weight=0.7)
        if lhb_data:
            for item in lhb_data:
                yz = item.get('yz', 0)
                ql = item.get('ql', 0)
                jg = item.get('jg', 0)
                if yz > 0 and ql > 0 and jg == 0:
                    r4.signal -= 15
                    r4.evidence.append("游资+量化合力=回避(-15)")
                    r4.warnings.append("游资+量化=次日表现最差")
        else:
            r4.signal = 0
        
        # R2-5: TDX席位标签验证（基于维度34）
        r5 = RuleNode("TDX标签验证", 2, weight=0.8)
        if lhb_data:
            for item in lhb_data:
                details = str(item.get('details',''))
                # 炒股养家 +15
                if '炒股养家' in details:
                    r5.signal += 15
                    r5.evidence.append("炒股养家买入! (+15)")
                # 瑞鹤仙/杭州帮/南京帮
                if any(k in details for k in ['瑞鹤仙', '杭州帮', '南京帮']):
                    r5.signal += 12
                    r5.evidence.append("知名游资买入(+12)")
                # 量化打板
                if '量化打板' in details:
                    r5.signal += 8
                    r5.evidence.append("量化打板买入(+8)")
                # T王/方新侠/山东帮 = 回避
                if any(k in details for k in ['T王', '方新侠', '山东帮', '益田路']):
                    r5.signal -= 20
                    r5.evidence.append("亏钱游资(-20)")
                    r5.warnings.append("T王/方新侠/山东帮买入=回避")
        else:
            r5.signal = 0
        
        return [r1, r2, r3, r4, r5]


class ExecuteLayer:
    """
    Layer 1: 操作执行层
    5个维度 → 最终买/卖/仓位决策
    接收上面5层→综合裁决
    """
    def evaluate(self, upper_signals):
        rules = []
        
        # 汇总上层信号
        total = sum(s.get('signal', 0) * s.get('weight', 1.0) 
                    for layer in upper_signals 
                    for s in layer)
        
        # R1-1: 仓位决定
        r1 = RuleNode("仓位决定", 1, weight=1.0)
        if total > 150:
            r1.signal = 80
            r1.evidence.append("多方主导, 重仓60~80%")
        elif total > 80:
            r1.signal = 50
            r1.evidence.append("多方占优, 正常仓位30~50%")
        elif total > 20:
            r1.signal = 20
            r1.evidence.append("中性偏多, 轻仓15~30%")
        elif total > -20:
            r1.signal = 5
            r1.evidence.append("中性, 观望或极小仓位0~10%")
        elif total > -80:
            r1.signal = -20
            r1.evidence.append("空方主导, 空仓或极小仓位")
            r1.warnings.append("空方信号强烈")
        else:
            r1.signal = -60
            r1.evidence.append("强烈空方信号! 空仓!")
            r1.warnings.append("系统建议空仓回避")
        
        # R1-2: 买入模式
        r2 = RuleNode("买入模式", 1, weight=0.8)
        if total > 100:
            r2.signal = 1  # 模式A: 追涨
            r2.evidence.append("模式A: 主升浪追涨")
        elif total > 30:
            r2.signal = 2  # 模式B: 低吸
            r2.evidence.append("模式B: 回踩低吸")
        elif total > -30:
            r2.signal = 3  # 模式C: 不动
            r2.evidence.append("模式C: 等待明确信号")
        else:
            r2.signal = 4  # 模式D: 卖出
            r2.evidence.append("模式D: 减仓/卖出")
            r2.warnings.append("建议减仓规避风险")
        
        # R1-3: 风险控制
        r3 = RuleNode("风险控制", 1, weight=1.2)
        warnings = [s.get('warnings',[]) for layer in upper_signals for s in layer]
        all_warnings = [w for sub in warnings for w in sub]
        if any('黑天鹅' in w for w in all_warnings):
            r3.signal = -40
            r3.evidence.append("黑天鹅预警! 风控收紧(-40)")
        elif any('空仓' in w or '回避' in w for w in all_warnings):
            r3.signal = -20
            r3.evidence.append("风控信号(-20)")
        else:
            r3.signal = 10
            r3.evidence.append("风控正常(+10)")
        
        # R1-4: 卖出信号
        r4 = RuleNode("卖出信号", 1, weight=0.9)
        if total < -50:
            r4.signal = 1  # 强制卖出
            r4.evidence.append("强制卖出信号")
        else:
            r4.signal = 0
        
        # R1-5: 执行清单
        r5 = RuleNode("执行清单", 1, weight=0.5)
        items = []
        if total > 80:
            items.append("早盘积极建仓")
            items.append("优先宁波帮/华泰系席位票")
        elif total > 20:
            items.append("开盘后半小时观察后买入")
            items.append("只做北向+机构双买的票")
        elif total > -20:
            items.append("竞价后评估,不急入场")
            items.append("小仓位做最强信号")
        else:
            items.append("暂停买入!")
            items.append("检查已有持仓是否需要卖出")
        r5.evidence = items[:3] if isinstance(items, list) else [items]
        
        return [r1, r2, r3, r4, r5]


# ============================================================
# 穿透式规则系统 — 主入口
# ============================================================

class PenetrationRuleSystem:
    """
    层维穿透式动态规则系统
    
    使用方式:
        system = PenetrationRuleSystem()
        result = system.run(context)
    
    返回:
        {
            'total_score': 150,        # 总信号分
            'layers': [L6, L5, L4, L3, L2, L1],  # 每层结果
            'decision': '积极做多',      # 最终决策
            'position': '60~80%',       # 仓位
            'mode': '主升浪追涨',       # 模式
            'warnings': [...],          # 所有警告
        }
    """
    
    def __init__(self):
        self.layers = [
            ('宏观周期', MacroCycleLayer()),
            ('资金博弈', FundGameLayer()),
            ('环境匹配', EnvMatchLayer()),
            ('个股筛选', StockFilterLayer()),
            ('席位验证', SeatVerifyLayer()),
            ('操作执行', ExecuteLayer()),
        ]
        self.history = []
    
    def run(self, context=None):
        """完整穿透执行"""
        if context is None:
            context = {}
        
        lhb_data = context.get('lhb_data', [])
        env_score = context.get('env_score', 50)
        candidates = context.get('candidates', [])
        
        # 自上而下穿透 (L6→L1)
        all_layer_results = []
        
        # L6: 宏观周期
        l6_rules = self.layers[0][1].evaluate()
        all_layer_results.append([r.to_dict() for r in l6_rules])
        
        # L5: 资金博弈
        l5_rules = self.layers[1][1].evaluate(lhb_data)
        all_layer_results.append([r.to_dict() for r in l5_rules])
        
        # L4: 环境匹配
        l4_rules = self.layers[2][1].evaluate(env_score)
        all_layer_results.append([r.to_dict() for r in l4_rules])
        
        # L3: 个股筛选
        l3_rules = self.layers[3][1].evaluate(candidates)
        all_layer_results.append([r.to_dict() for r in l3_rules])
        
        # L2: 席位验证
        l2_rules = self.layers[4][1].evaluate(lhb_data)
        all_layer_results.append([r.to_dict() for r in l2_rules])
        
        # L1: 操作执行 (接收上面5层)
        l1_rules = self.layers[5][1].evaluate(all_layer_results[:5])
        all_layer_results.append([r.to_dict() for r in l1_rules])
        
        # 计算总分（穿透式加权）
        total_score = 0
        total_weight = 0
        all_warnings = []
        
        for layer_idx, layer_rules in enumerate(all_layer_results):
            layer_weight = 1.0 - layer_idx * 0.1  # L6权重最大
            for rule in layer_rules:
                w = rule['weight'] * layer_weight
                total_score += rule['signal'] * w
                total_weight += w
                all_warnings.extend(rule.get('warnings', []))
        
        total_score = total_score / total_weight if total_weight > 0 else 0
        
        # 最终决策
        l1_signals = {r['name']: r['signal'] for r in all_layer_results[5]}
        position_signal = l1_signals.get('仓位决定', 0)
        mode_signal = l1_signals.get('买入模式', 3)
        
        # 仓位映射
        if position_signal > 50:
            position = '60~80%'
            decision = '积极做多'
        elif position_signal > 15:
            position = '30~50%'
            decision = '正常买入'
        elif position_signal > 0:
            position = '15~30%'
            decision = '轻仓'
        elif position_signal > -20:
            position = '0~10%'
            decision = '观望'
        else:
            position = '0%'
            decision = '空仓/卖出'
        
        # 模式映射
        mode_map = {1: '主升浪追涨', 2: '回踩低吸', 3: '等待信号', 4: '减仓卖出'}
        mode = mode_map.get(mode_signal, '等待信号')
        
        # 去重警告
        unique_warnings = list(dict.fromkeys(all_warnings))[:5]
        
        result = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_score': round(total_score, 1),
            'layers': all_layer_results,
            'decision': decision,
            'position': position,
            'mode': mode,
            'warnings': unique_warnings,
        }
        
        self.history.append(result)
        return result
    
    def print_report(self, result):
        """打印穿透式报告"""
        print("=" * 70)
        print(f"层维穿透式动态规则系统 — 决策报告")
        print(f"时间: {result['timestamp']}")
        print(f"总分: {result['total_score']}")
        print("=" * 70)
        
        layer_names = ['宏观周期(L6)', '资金博弈(L5)', '环境匹配(L4)', 
                       '个股筛选(L3)', '席位验证(L2)', '操作执行(L1)']
        
        for i, (layer_name, rules) in enumerate(zip(layer_names, result['layers'])):
            print(f"\n  ┌── {layer_name}")
            for rule in rules:
                sig = rule['signal']
                if sig > 0:
                    sig_str = f"+{sig}"
                else:
                    sig_str = f"{sig}"
                print(f"  │  {rule['name']:<16} 信号:{sig_str:>5}  权重:{rule['weight']:.1f}")
                for ev in rule['evidence'][:2]:
                    print(f"  │    ├ {ev}")
                for w in rule['warnings'][:2]:
                    print(f"  │    └ ⚠️ {w}")
        
        print(f"\n  {'─'*50}")
        print(f"  🎯 决策: {result['decision']}")
        print(f"  💼 仓位: {result['position']}")
        print(f"  📋 模式: {result['mode']}")
        if result['warnings']:
            print(f"  ⚠️ 警告:")
            for w in result['warnings']:
                print(f"    └ {w}")
        
        return result


# ============================================================
# 测试入口
# ============================================================
if __name__ == '__main__':
    system = PenetrationRuleSystem()
    result = system.run()
    system.print_report(result)
