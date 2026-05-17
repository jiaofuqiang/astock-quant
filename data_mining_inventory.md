# A股量化系统 — 数据挖掘总账
> 更新时间: 2026-05-16

## Bundle Keys 演变

| 阶段 | Keys | 总大小 | 新增内容 |
|:----:|:----:|:------:|---------|
| 🏁 初始 | **51** | 623KB | 基础架构 |
| 📊 第一轮 | **53** | 632KB | +东财龙虎榜桥接 +板块预警 +全球行情 |
| 📈 第二轮 | **57** | 648KB | +北交所 +板块资金 +龙虎榜网络 +可转债 +跟风信号 +融资融券 |

## 本轮新增数据源 (9个)

| # | Key | 数据内容 | 脚本 |
|:-:|-----|---------|------|
| 1 | `em_lhb_summary` | 东财龙虎榜4维度摘要(游资/机构/频率/收益) | `em_lhb_bundle_bridge.py` |
| 2 | `sector_alerts` | 板块预警信号(过热/退潮/衰退/轮动) 5条活跃 | `sector_alert_generator.py` |
| 3 | `global_market_deep` | 10美股+4ETF+5港股实时行情 | `global_market_deep_collector.py` |
| 4 | `bse_market` | 北交所10只实时行情 | `bse_market_collector.py` |
| 5 | `sector_fund_flow` | 板块+概念资金流向(东财,已框待数据) | `sector_fund_flow_collector.py` |
| 6 | `lhb_seat_network` | 龙虎榜席位网络(协同/抱团/风格分类) | `lhb_seat_network_analyzer.py` |
| 7 | `cb_market` | 可转债10只行情+转股溢价率 | `cb_market_collector.py` |
| 8 | `follower_signals` | 跟风信号8只(置信度0.47-0.74) | `follower_signal_generator.py` |
| 9 | `margin_debt` | 融资融券TOP增减各10(东财500条) | `margin_debt_collector.py` |

## 架构清理
- 🗑️ 删除31个空壳数据库文件
- 📐 数据架构文档: `data_architecture_v2.md`
- 🔄 穿透评分: 48维v1 → 25维v2(合成去冗余)

## 待开盘自动恢复
- global_market_deep 美股行情(非交易时段空)
- sector_fund_flow 板块资金(东财API限流)

## 新增脚本清单
```
/home/ubuntu/astock/scripts/
├── em_lhb_bundle_bridge.py          # 东财龙虎榜桥接
├── sector_alert_generator.py        # 板块预警引擎
├── global_market_deep_collector.py  # 全球行情深度
├── bse_market_collector.py          # 北交所行情
├── sector_fund_flow_collector.py    # 板块资金流向
├── lhb_seat_network_analyzer.py     # 龙虎榜席位网络
├── cb_market_collector.py           # 可转债行情
├── follower_signal_generator.py     # 跟风信号生成
└── margin_debt_collector.py         # 融资融券采集
```
