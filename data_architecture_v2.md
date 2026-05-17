# A股量化系统 — 数据架构总览 v2
> 更新: 2026-05-16
> 状态: 34DB → 36DB(有效) + 31DB已清理, 51keys → 53keys

## 数据源拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                        六大数据源平台                                │
├───────────┬──────────┬──────────┬────────┬────────┬──────────────┤
│  腾讯行情   │ 东方财富  │ 同花顺   │ 新浪财经 │华尔街见闻│ 韭研公社    │
│  qt.gtimg  │ push2/api │10jqka   │ finance │ wallstcn│ jiuyangongshe│
│ 实时/封单/  │ 板块/资金/ │概念/涨停 │ 龙虎榜/ │ 快讯/   │ 涨停原因    │
│ 竞价/美股   │ 龙虎榜/   │ 原因     │ 新闻    │ 文章    │ (浏览器采集) │
│    40+脚本  │ 股东/公告  │         │         │         │             │
└─────────────┴──────────┴──────────┴────────┴────────┴──────────────┘
```

## 数据库清单 (36个活跃, 2.0GB总量)

### 核心库
| 数据库 | 大小 | 核心表 | 用途 |
|--------|------|--------|------|
| `sector_indexes.db` | 939M | sector_daily_index, sector_correlation, rotation_log | 板块指数/联动 |
| `factor_v6.db` | 769M | factors(150+列) | V6因子数据 |
| `kline_cache.db` | 268M | kline, stock_info | 全量日K线(3239只) |
| `ban_order.db` | 1.9M | ban_order | 封单数据 |
| `daily_limit_data.db` | 3.4M | limit_stocks | 涨停明细 |
| `lhb_cache.db` | 6.5M | lhb_list, lhb_detail | 龙虎榜(新浪) |
| `stock_profiles.db` | 6.0M | concepts/stock_basic/valutions | 个股档案 |
| `chain_engine.db` | 520K | stock_chain, chain_summary | 产业链传导 |
| `em_lhb_cache.db` | 56K | stock_lhb_stats/dept_return_rank/... | 东财龙虎榜增强 |
| `time_slice_history.db` | 1.8M | time_slices/feature_vector | 时间片快照 |
| `market_daily.db` | 16K | day_full(62字段) | 每日市场全景 |
| `global_market.db` | 28K | us_index/stock/commodity | 全球市场 |

### 辅助库
market_index.db, industry_trend.db, topic_tree.db, macro_cache.db, 
holder_cache.db, fundamental.db, news_cache.db, trade_sim.db,
strategy_verify.db, strategy_pool.db, tetegu_cache.db, astock_new.db,
limit_order_history.db, factor_cache.db, factor_v8.db

### ✅ 已清理的废弃DB (31个)
空壳(astock根目录7个) + data下废弃(21个) + 其他(3个)
含: older_version_db(limits_v2/v3, limit_up_v3等), auction, lhb_data, 
  three_funds, sector_index(旧版), time_slices, macor_cache(拼写错误)

## Bundle Keys (53个)

### 核心组 (一直存在)
_meta, market_env, market_daily, market_index, market_clusters,
brent_oil, us_market_map, us_dual, sector_ranking, sector_cycle,
sector_micro, sector_decisions, time_slice, auction, auction_decision,
watch_dashboard, buy_candidates, top_strategies, all_strategies,
memory_strategies, buy_signal, retail_sentiment, youzi_signal,
follower_signals, f2_data, lhb_scoring, lhb_selected_features,
lhb_selected_detail, holder_ambush, news, news_sentiment, news_concepts,
decision_report, closing_report, strategy_rank, dynamic_9layer,
contradiction_report, ban_reasons, jiuyang, limit_reasons,
huanshouban, night_pool, scan_data, trading_manual, sector_index,
watch_pool, new_concepts, trade, auction_bidding_raw

### ✅ 本次新增 (3个)
| Key | 源 | 说明 |
|-----|----|------|
| `em_lhb_summary` | em_lhb_bundle_bridge.py | 东财龙虎榜增强摘要(4维度) |
| `sector_alerts` | sector_alert_generator.py | 板块预警信号(过热/退潮/衰退/轮动) |
| `global_market_deep` | global_market_deep_collector.py | 全球市场深度(10美股+4ETF+5港股) |

### 注意: 以下字段框架存在但可能空
- `sector_alerts` → 已激活(5条预警)
- `follower_signals` → 仍为空(需后续激活)
- `f2_data` → 仅时间戳

## Cron任务 (当前活跃3条)
```
*/5 9-14 * * 1-5  time_slice_collector.py  (每5分钟快照)
*/5 15 * * 1-5    time_slice_collector.py  (下午续采)
5 15 * * 1-5      market_daily_integrator.py (日终整合)
```

## 数据采集管道状态

### 实时层 (每5分钟)
time_slice_collector → time_slice_history.db → bundle.time_slice

### 盘中层 (聚合器每300s)
- 读取: bundle50+个数据源
- 运行: 穿透评分v2(25维)
- 运行: em_lhb_bridge (东财龙虎榜)
- 运行: sector_alert (板块预警)
- 运行: global_market_deep (全球行情)
- 输出: dashboard_bundle.json(53keys, 632KB)
- 展示: dashboard.html(端口80)

### 日终层 (15:05)
market_daily_integrator → market_daily.db (62字段全景)
