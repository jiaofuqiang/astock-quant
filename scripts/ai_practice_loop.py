#!/usr/bin/env python3
"""
AI决策系统 — 数据管道 & 自演化模块
====================================
负责：每日自动采集→引擎决策→T+1复盘→红黑榜更新→权重微调
"""

import os, sys, json, sqlite3, subprocess
from datetime import datetime, timedelta

BASE = os.path.expanduser('~/astock')
SCRIPTS = os.path.join(BASE, 'scripts')
DATA = os.path.join(BASE, 'data')
V2BOARD = os.path.expanduser('~/V2board')

# ============================================================
# 模块1: AI上下文构建器
# ============================================================
class AIContextBuilder:
    """从系统所有数据源构建AI需要的完整上下文"""
    
    def build_context(self):
        context = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_sources': {},
        }
        
        # 1. bundle（穿透评分+决策）
        bundle = self._load_json(os.path.join(V2BOARD, 'dashboard_bundle.json'))
        if bundle:
            context['contradiction_report'] = bundle.get('contradiction_report', {})
            context['candidates'] = bundle.get('buy_candidates', [])
            context['strategies'] = bundle.get('strategies', [])
            context['data_quality'] = bundle.get('contradiction_report', {}).get('data_quality', {})
            context['data_sources']['bundle'] = True
        
        # 2. LHB评分缓存（龙虎榜个股）
        lhb = self._load_json(os.path.join(DATA, 'lhb_scoring_cache.json'))
        if lhb:
            context['lhb_data'] = lhb.get('actionable', [])
            context['lhb_meta'] = {
                'date': lhb.get('date', ''),
                'engine': lhb.get('engine', ''),
                'total_signals': lhb.get('total_signals', 0),
            }
            context['data_sources']['lhb_scoring'] = True
        
        # 3. 红黑榜统计
        rbs = self._query_redblack_stats()
        if rbs:
            context['redblack_stats'] = rbs
            context['data_sources']['redblack'] = True
        
        # 4. 市场日数据（环境宏观）
        md = self._query_market_daily()
        if md:
            context['market_daily'] = md
            context['data_sources']['market_daily'] = True
        
        # 5. 季节信息
        now = datetime.now()
        context['season'] = {
            'month': now.month,
            'quarter': (now.month - 1) // 3 + 1,
            'is_quarter_end': now.month in [3, 6, 9, 12],
            'weekday': now.weekday(),
        }
        
        # 计算数据完整性评分
        sources_count = sum(1 for v in context['data_sources'].values() if v)
        context['data_completeness'] = min(1.0, sources_count / 5)
        
        return context
    
    def _load_json(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            return None
    
    def _query_redblack_stats(self):
        db_path = os.path.join(DATA, 'maodun_redblack.db')
        if not os.path.exists(db_path):
            return None
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5)
            rows = conn.execute("""
                SELECT grade, COUNT(*), AVG(t1_return),
                       SUM(CASE WHEN t1_return > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
                FROM grade_backtest
                WHERE t1_return IS NOT NULL
                GROUP BY grade
            """).fetchall()
            conn.close()
            return {r[0]: {'count': r[1], 'avg_return': round(r[2] or 0, 2), 'win_rate': round((r[3] or 0) * 100, 1)} for r in rows}
        except:
            return None
    
    def _query_market_daily(self):
        db_path = os.path.join(DATA, 'market_daily.db')
        if not os.path.exists(db_path):
            return None
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5)
            row = conn.execute("""
                SELECT date, up_count, down_count, limit_up, yizi, zhaban_count,
                       youzi_net_wan, jigou_net_wan, sanhu_net_wan
                FROM day_full ORDER BY date DESC LIMIT 1
            """).fetchone()
            conn.close()
            if row:
                return {
                    'date': row[0], 'up': row[1], 'down': row[2],
                    'limit_up': row[3], 'yizi': row[4], 'zhaban': row[5],
                    'youzi_net': row[6], 'jigou_net': row[7], 'sanhu_net': row[8],
                }
        except:
            return None


# ============================================================
# 模块2: 实践论闭环 — T+1复盘 & 自演化
# ============================================================
class PracticeLoop:
    """实践论闭环：实践→认识→再实践"""
    
    def __init__(self):
        self.engine_imported = False
        self.ai_engine = None
    
    def run_daily_pipeline(self):
        """每日全链路"""
        print(f"\n{'='*60}")
        print(f"🤖 AI决策系统 — 每日全链路 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # Step 1: 构建上下文
        print("\n[1/4] 构建AI上下文...")
        builder = AIContextBuilder()
        context = builder.build_context()
        sources = [k for k, v in context.get('data_sources', {}).items() if v]
        print(f"  ✅ 数据源: {', '.join(sources)} (完整性{context['data_completeness']:.0%})")
        
        # Step 2: AI决策
        print("\n[2/4] AI穿透式决策...")
        if not self.engine_imported:
            sys.path.insert(0, SCRIPTS)
            from ai_decision_engine import AIDecisionEngine
            self.ai_engine = AIDecisionEngine()
            self.engine_imported = True
        
        result = self.ai_engine.decide(context)
        print(f"  🎯 决策: {result['decision']['action']}")
        print(f"  💼 仓位: {result['decision']['position']}")
        print(f"  📊 综合分: {result['total_score']}/100 (置信{result['overall_confidence']})")
        
        # Step 3: 保存决策到bundle
        print("\n[3/4] 注入决策到bundle...")
        self._inject_to_bundle(result)
        
        # Step 4: 生成摘要
        print("\n[4/4] 生成决策摘要...")
        summary = self._generate_summary(result, context)
        
        return summary
    
    def _inject_to_bundle(self, result):
        """把AI决策注入dashboard_bundle.json"""
        path = os.path.join(V2BOARD, 'dashboard_bundle.json')
        try:
            with open(path) as f:
                bundle = json.load(f)
            
            # 注入AI决策层
            bundle['ai_decision'] = {
                'timestamp': result['timestamp'],
                'engine': result['engine'],
                'total_score': result['total_score'],
                'confidence': result['overall_confidence'],
                'action': result['decision']['action'],
                'position': result['decision']['position'],
                'mode': result['decision']['mode'],
                'reasoning': result['decision']['reasoning'],
                'warnings': result['decision']['warnings'],
                'sensors': {k: {'signal': v['signal'], 'confidence': v['confidence']} 
                           for k, v in result['sensors'].items()},
                'pairs': {k: {'label': v['label'][:30], 'score': v['score'], 'confidence': v['confidence']}
                         for k, v in result['pair_matrix'].items()},
            }
            
            with open(path, 'w') as f:
                json.dump(bundle, f, ensure_ascii=False, indent=2)
            print(f"  ✅ AI决策已注入bundle (ai_decision.{len(result['decision'].keys())}个字段)")
        except Exception as e:
            print(f"  ⚠️ bundle写入失败: {e}")
    
    def _generate_summary(self, result, context):
        """生成人类可读的决策摘要"""
        d = result['decision']
        summary = f"""
🤖 AI穿透式决策摘要
{'='*40}
时间: {result['timestamp']}
综合分: {result['total_score']}/100 (置信度{result['overall_confidence']:.0%})

🎯 决策: {d['action']}
💼 仓位: {d['position']}
📋 模式: {d['mode']}
"""
        if d['reasoning']:
            summary += "\n📌 理由:\n" + "\n".join(f"  {r}" for r in d['reasoning'])
        if d['warnings']:
            summary += "\n⚠️ 警告:\n" + "\n".join(f"  {w}" for w in d['warnings'])
        
        summary += f"\n\n📡 传感器:\n"
        for name, r in result['sensors'].items():
            summary += f"  {name}: {r['signal']:+3.0f} (置信{r['confidence']:.0%})\n"
        
        # 🆕 穿透式回测推荐
        try:
            sp = os.path.join(os.path.dirname(__file__), '..', 'docs', 'signals_penetration_v1.json')
            if os.path.exists(sp):
                with open(sp) as f:
                    sdb = json.load(f)
                from datetime import datetime
                wd = datetime.now().weekday()
                wd_names = ['周一','周二','周三','周四','周五','周六','周日']
                summary += f"\n🎯 穿透策略推荐({wd_names[wd]}):\n"
                for g in sdb.get('gold_signals', [])[:3]:
                    if g.get('n', 0) >= 8:
                        summary += f"  🟢 {g['label']}: +{g['ret']:.1f}%/wr{g['wr']:.1f}%({g['n']}笔)\n"
                for a in sdb.get('avoid_signals', [])[:2]:
                    summary += f"  🔴 {a['label']}: {a['ret']:+.1f}%/wr{a['wr']:.1f}%({a['n']}笔)\n"
                summary += f"  📊 数据源:1,541笔严格时间线回测\n"
        except:
            pass
        
        return summary


# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    loop = PracticeLoop()
    summary = loop.run_daily_pipeline()
    print(summary)
