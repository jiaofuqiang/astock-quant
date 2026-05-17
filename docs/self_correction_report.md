# 全量自纠错报告

## 第三轮：杂物大清理 (2026-05-17 17:00)

### 清理清单

| 类型 | 清理内容 | 数量 |
|:----|:---------|:-----|
| data/无引用JSON | backtest_lhb_strategy_v1.json, lhb_4_strategy.json, lhb_factor_v7.json, contradiction_backtest.json/v2, funds_backtest_results.json, funds_score_log.json, closing_report_push.json, daily_push.json, baostock_minute_stats.json, global_market_deep.json, holder_signal_cache.json, intraday_buy_signals.json, intraday_signals.json | **14个** |
| 根目录散生脚本 | v6_close_review.py/v2/v3, v8_full_engine.py, v8_now.py, cron_scan.py, signal_track.py, _check_ban*.py, tmp_*.py, itick_test_script.sh, monday_open_sequence.sh | **15个** |
| 根目录旧文档 | backtest_plan.md, backtest_report.md, layer_dimension_analysis.md, layer_dimension_map.html, V2.0_fix_status.md, data_mining_inventory.md, 周一操作备忘.txt | **7个** |
| 根目录旧TXT | f2_weipan_data.txt, retail_sentiment.txt, scan_data.txt, watch_pool.txt, new_concept_data.txt, sentiment_cycle.json | **6个** |
| output/旧日志 | 全部旧版日报(5/2~5/15), v8日志, 旧valution输出, 旧market扫描 | **~200个** |
| research/研究产物 | lhb_firstboard_factor_research.json(3.6M), market_history_v*.json(3.2M), market_strategy*.json(1.7M), daban/highboard/huanshouban/sellsignal所有版本, 旧txt/md报告 | **55个** |
| 无引用目录 | qlib/ (48MB), jygs_images/ (6.9M), strategy_failure_monitor/, charts/ (1.2M), temp/, outputs/, dashboard_data/ (部分) | **7个目录** |
| **总计** | | **~300个文件** |

### 磁盘
- 清理前: 17G/40G (43%)
- 清理后: 17G/40G (43%) — 数据集约在sqlite数据库，清理的都是文本/JSON小文件

### 核心保留
- scripts/ 128个py脚本 (所有cron和engine)
- data/ 201个文件 (含14个sqlite + 85个json + 其他)
- docs/ 13个文档
- V2board/ dashboard bundle
- 25个deprecated文件标记为.deprecated留在scripts/（无删除风险）

### 明天08:30 三刀流v3
morning_pipeline.py ✅ 编译通过
数据库 kline_cache.db(520d) + market_daily.db(570d) + lhb_cache.db(3,113笔) 齐全
cron job "早盘三刀流v3" 正常
