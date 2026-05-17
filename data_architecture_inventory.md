# A股量化系统 — 数据架构全面盘点

> 生成时间: 2026-05-16 09:15
> 盘点范围: astock + V2board 全量数据管道

---

## 一、数据源API清单

### 1.1 腾讯行情 (核心实时数据源) — `qt.gtimg.cn`

| API | 用途 | 采集频率 | 采集团 |
|------|------|---------|-------|
| `http://qt.gtimg.cn/q={codes}` | A股实时行情(批量) | 每5分钟 | time_slice_collector, ban_order_collector, cloud_trading, 等 ~40个脚本 |
| `http://qt.gtimg.cn/q=us{ticker}` | 美股实时行情 | 每日/盘中 | daily_anchor_data, macro_collector, global_collector |
| `http://qt.gtimg.cn/q=hf_{symbol}` | 商品期货行情 | 每日 | macro_collector |
| `http://qt.gtimg.cn/q=sh000001,sz399001,sz399006` | 三大指数 | 每5分钟 | 多处调用 |
| `https://ifzq.gtimg.cn/appstock/app/kline/mkline` | 30分钟K线历史 | 回采时 | fill_kline_gap系列, 1ban_deep_v11 |
| `https://ifzq.gtimg.cn/appstock/app/kline/kline` | 日K线历史 | 回采时 | fill_kline_gap系列 |

### 1.2 东方财富 (核心数据源)

| API | 用途 | 采集频率 |
|------|------|---------|
| `https://push2.eastmoney.com/api/qt/clist/get` | 板块排行/涨停池/行业排行 | 每5分钟~每日 |
| `https://push2.eastmoney.com/api/qt/stock/get` | 个股详细行情(含主力) | 盘中实时 |
| `https://push2.eastmoney.com/api/qt/ulist.np/get` | 个股资金流 | 盘中 |
| `https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get` | 资金流向日K线 | 每日 |
| `https://datacenter.eastmoney.com/securities/api/data/v1/get` | 龙虎榜明细/产业链/涨停数据 | 每日收盘后 |
| `https://datacenter-web.eastmoney.com/api/data/v1/get` | 十大流通股东/股东新进退出 | 每日 |
| `https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax` | 个股主营业务分析 | 按需 |
| `https://np-anotice-stock.eastmoney.com/api/security/ann` | 上市公司公告 | 每日 |
| `https://np-weblist.eastmoney.com/comm/web/getFastNewsList` | 财经快讯 | 每日盘前 |

### 1.3 同花顺

| API | 用途 |
|------|------|
| `https://yuanchuang.10jqka.com.cn/mrnxgg_list/` | 异动股揭秘(涨停原因) |
| `https://m.10jqka.com.cn/stock/bkfy_list/` | 行业新闻 |
| `https://basic.10jqka.com.cn/{code}/field.html` | 个股概念/基本面 |
| `https://basic.10jqka.com.cn/{code}/concept.html` | 个股概念列表 |
| `https://basic.10jqka.com.cn/api/stockph/basic/{code}/basicInfo.shtml` | 个股基本信息API |
| `https://stockpage.10jqka.com.cn/{ticker}/` | 美股个股行情页(NVDA等) |

### 1.4 新浪财经

| API | 用途 |
|------|------|
| `https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml` | 龙虎榜列表页 |
| `https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&num={num}` | 新浪滚动新闻(财经/美股/科技/宏观等8频道) |
| `https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/000001.phtml` | 上证综指要闻 |

### 1.5 华尔街见闻

| API | 用途 |
|------|------|
| `https://api-one.wallstcn.com/apiv1/content/lives` | 实时快讯 |
| `https://api-one.wallstcn.com/apiv1/content/articles` | 精选文章 |

### 1.6 韭研公社

| 来源 | 用途 |
|------|------|
| `https://www.jiuyangongshe.com/u/{user_id}` | 涨停原因/板块分类(Playwright采集) |
| 手动浏览器导出 | 异动页面数据 |

### 1.7 搜狐K线

| API | 用途 |
|------|------|
| `https://q.stock.sohu.com/hisHq?code=...` | 历史K线回补(少量使用) |

### 1.8 财联社

| 来源 | 用途 |
|------|------|
| 财联社电报页(SSR, Playwright) | 盘前快讯采集 |

### 1.9 Baostock (免费开源证券数据)

| 用途 |
|------|
| 历史K线/分钟线/基本面数据(回补历史) |

### 1.10 其他

| 来源 | 用途 |
|------|------|
| `https://data.eastmoney.com/stock/lhb.html` | 东方财富龙虎榜(Playwright) |
| 自定义浏览器采集 | 部分无API页面 |

---

## 二、数据库文件及用途 (34个活跃DB, 27个空/废弃DB)

### 核心数据库

| 数据库 | 大小 | 表 | 用途 |
|--------|------|----|------|
| `sector_indexes.db` | **939M** | sector_daily_index, sector_correlation, sector_rotation_log, sector_leader_stats, sector_stock_daily, sector_follower_backtest, tdx_sector_raw | 板块指数、联动分析、轮动监控 — 数据量最大 |
| `factor_v6.db` | **769M** | factors | V6版因子数据(全量个股日频, ~150列因子) |
| `kline_cache.db` | **268M** | kline, stock_info | 全量日K线缓存 |
| `factor_cache.db` | 8.1M | factors | V1版因子数据 |
| `factor_v8.db` | 7.9M | factors | V8版因子数据(含多周期收益率) |
| `lhb_cache.db` | 6.5M | lhb_list, lhb_detail | 新浪龙虎榜数据 |
| `stock_profiles.db` | 6.0M | concepts, concept_heat, stock_basic, financials, valuations, strategy_perf, 等24表 | 个股档案、概念、基本面、估值 |
| `industry_trend.db` | 4.9M | industry_timeline, topic_analysis, topic_indices | 产业链时间线/分析 |
| `daily_limit_data.db` | 3.4M | limit_stocks, limit_stocks_v2, limit_strength | 涨停数据(首封时间/连板/封板率) |
| `astock.db` | 4.5M | daily_kline | 日K线(hermes目录) |
| `astock_new.db` | 4.1M | daily_kline | 日K线新库 |
| `ban_order.db` | 1.9M | ban_order | 封单数据(腾讯行情采集) |
| `time_slice_history.db` | 1.8M | time_slices, dim_*, feature_vector, tag_clusters, prediction_log, snapshot_log | 5分钟时间片快照(7维度+特征向量+预测) |
| `topic_tree.db` | 1.1M | topics, topic_concepts, topic_stocks | 产业链主题树 |
| `tetegu_cache.db` | 696K | limit_reasons, limit_genes, limit_niusan, market_emotion | 涨停基因/原因/牛散标记 |
| `holder_cache.db` | 628K | holder_new, holder_new_person, holder_quit_person, holder_backtest, bulls_eye | 十大流通股东新进/退出追踪 |
| `market_index.db` | 536K | index_daily | 市场指数日数据 |
| `chain_engine.db` | 520K | stock_chain, stock_chain_v2, chain_summary | 产业链传导引擎 |
| `limit_order_history.db` | 396K | limit_daily, limit_orders | 涨停封单历史明细 |
| `fundamental.db` | 288K | stock_basic, profit_data, balance_data, cash_flow_data, growth_data | 基本面数据 |
| `trade_sim.db` | 288K | account, positions, trades, signals, daily_snapshot | 回测交易模拟 |
| `news_cache.db` | 136K | news, money_flow, us_anchors | 新闻缓存+资金流+美股锚定 |
| `macro_cache.db` | 84K | macro_index_data, macro_key_stock | 外围市场(美股/商品) |
| `em_lhb_cache.db` | 56K | stock_lhb_stats, dept_return_rank, dept_lhb_rank, inst_buy_sell, inst_seat_track, daily_active_dept | 东方财富龙虎榜增强数据 |
| `strategy_verify.db` | 40K | verifications, recommendations, daily_summary, strategy_health | 策略验证 |
| `global_market.db` | 28K | us_index, us_stock, commodity | 全球市场(冗余于macro_cache) |
| `strategy_pool.db` | 24K | active_strategies, daily_validation, strategy_pool_log | 策略池管理 |
| `market_daily.db` | 16K | day_full | 每日市场全景 |

### 辅助/废弃DB (0字节 = 创建后未使用)

`limits_v2.db`, `limits_v3.db`, `limit_history.db`, `limit_up_v3.db`, `lhb_data.db`, `holder_new.db`, `funds_history.db`, `factors.db`, `dashboard.db`, `daily_limit.db`, `daily_limit_cache.db`, `concept_heat.db`, `ban_order_collector.db`, `auction.db`, `astock.db(顶层)`, `three_funds.db`, `stock_orders.db`, `sector_index.db`, `macor_cache.db`, `verify_records.db`, `time_slices.db`, `closing_reports.db`, `valuation_v2.db`, `signal_track.db`, `realtime_board.db`

---

## 三、定时采集任务 (Crontab)

```
5 15 * * 1-5   cd /home/ubuntu/astock && python3 scripts/market_daily_integrator.py
                → 每日15:05 整合市场日数据
*/5 9-14 * * 1-5  cd /home/ubuntu/astock && python3 scripts/time_slice_collector.py
                → 09:00~14:55 每5分钟时间片快照
*/5 15 * * 1-5   cd /home/ubuntu/astock && python3 scripts/time_slice_collector.py
                → 15:00~15:55 每5分钟时间片快照(下午)
```

**已安装但可能不活跃的cron任务** (通过脚本自安装):
- `em_lhb_collector.py --install-cron` → 每日16:30 龙虎榜采集
- `holder_new_collector.py --install-cron` → 每日收盘后 股东数据采集
- `lhb_collector.py --daily-cron` → 每日增量龙虎榜
- `sector_rotation_alert.py` → 每30分钟板块联动预警(9-15点)

---

## 四、Dashboard Bundle 结构 (51 Keys)

```
_meta              — 元数据(时间戳/版本)
brent_oil          — 布伦特原油行情
scan_data          — 全市场扫描文本(str)
market_env          — 市场环境评分
sector_index       — 板块指数数据
ban_reasons        — 涨停原因(自有+韭研融合)
us_market_map      — 美股映射
market_daily       — 大盘日数据
market_clusters    — 市场聚类(冰点/狂热/活跃)
time_slice         — 时间片快照(最新)
trading_manual     — 交易手册(str)
lhb_scoring        — 龙虎榜评分
holder_ambush      — 股东伏击信号
news               — 财经新闻聚合
news_sentiment     — 新闻情绪分析
news_concepts      — 新闻→概念映射
sector_alerts      — 板块预警(当前空列表)
jiuyang            — 韭研公社涨停原因
sector_cycle       — 板块周期监控
us_dual            — 美股双模型
auction            — 竞价数据
sector_micro       — 板块微观信号
auction_decision   — 竞价决策
auction_bidding_raw — 竞价原始数据
watch_dashboard    — 作战面板(梯队/热点)
trade              — 交易账户数据
buy_signal         — 买入信号(str)
new_concepts       — 新概念发现(str)
watch_pool         — 观察池(str)
retail_sentiment   — 散户情绪反指(str)
youzi_signal       — 游资信号
follower_signals   — 跟风信号(str)
f2_data            — F2数据(str)
decision_report    — 决策报告
sector_decisions   — 板块决策
closing_report     — 收盘报告
strategy_rank      — 策略排名
dynamic_9layer     — 九维穿透动态层
huanshouban        — 换手板分析
night_pool         — 盘后股票池
lhb_selected_features — 龙虎榜精选特征
lhb_selected_detail   — 龙虎榜精选明细
contradiction_report  — 矛盾信号报告
contradiction_backtest — 矛盾回测结果
limit_reasons      — 涨停原因统计
market_index       — 市场指数(20只)
buy_candidates     — 买入候选(8只)
top_strategies     — TOP策略(5条)
sector_ranking     — 板块排名(10条)
memory_strategies  — 记忆策略(10条)
all_strategies     — 全量策略(18条)
```

---

## 五、数据流图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                             数据采集层                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  腾讯行情API ──→ time_slice_collector ──→ time_slice_history.db         │
│  (qt.gtimg.cn)    (每5分钟)              (7维+特征向量+快照)              │
│                                                                         │
│  腾讯行情API ──→ ban_order_collector  ──→ ban_order.db                 │
│                   (封单数据)                                              │
│                                                                         │
│  东方财富API ──→ limit_up_collector_v2 ──→ daily_limit_data.db         │
│  (datacenter)    (涨停数据)              (limit_stocks/limit_strength)  │
│                                                                         │
│  新浪/东方财富  ──→ news_monitor ──→ news_cache.db                     │
│  华尔街见闻      (全渠道新闻)           (news/money_flow/us_anchors)    │
│                                                                         │
│  新浪龙虎榜  ──→ lhb_collector ──→ lhb_cache.db                       │
│  东方财富龙虎榜 ──→ em_lhb_collector ──→ em_lhb_cache.db              │
│                                                                         │
│  东方财富概念API─→ fetch_concepts ──→ stock_profiles.db               │
│  同花顺概念API  ─→ fetch_ths_concepts  (concepts/concept_heat)         │
│                                                                         │
│  东方财富股东API─→ holder_new_collector ──→ holder_cache.db            │
│                                                                         │
│  腾讯行情API ──→ global_collector ──→ global_market.db                │
│                   macro_collector  ──→ macro_cache.db                  │
│                   (美股/商品期货)                                         │
│                                                                         │
│  韭研公社 ──→ jiuyang_scraper ──→ (JSON输出)                          │
│  同花顺移动  ──→ ths_collector ──→ ths_ban_reasons.json (涨停原因)     │
│                                                                         │
│  Baostock ──→ fetch_baostock_stats ──→ kline_cache.db (补历史K线)     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                             计算/分析层                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  sector_indexes.db ──→ sector_rotation_analysis (板块联动/轮动)        │
│                                                                         │
│  daily_limit_data  ──→ 涨停属性分析/连板统计/封板率                      │
│                                                                         │
│  time_slice_history──→ 九维穿透聚类(market_clusters)                    │
│                                                                         │
│  factor_v6/v8 ──→ 因子计算/回测/排名                                    │
│                                                                         │
│  lhb_cache ──→ lhb_scoring_engine (龙虎榜评分)                         │
│                                                                         │
│  holder_cache ──→ holder_ambush (股东伏击信号)                         │
│                                                                         │
│  all数据源 ──→ contradiction_engine (矛盾信号监测)                     │
│                                                                         │
│  all数据源 ──→ strategy_verify (策略验证)                              │
│                                                                         │
│  all数据源 ──→ market_daily_integrator ──→ market_daily.db (每日全景) │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                             展示层(V2board)                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  dashboard_aggregator (每30秒聚合) ──→ dashboard_bundle.json (51 keys) │
│         │                                                               │
│         ├── dashboard_builder.py (SSR渲染 → dashboard.html)            │
│         │                                                               │
│         ├── panel_server_80.py (HTTP服务端口80)                        │
│         │                                                               │
│         └── 微信推送: cron_push.py / 24h_close_loop 系列               │
│                                                                         │
│  server_v5/v6 (WebSocket实时推送)                                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 六、当前已采集但可能未充分利用的字段/数据

### 6.1 已采集但bundle中为空/未使用

| Bundle Key | 类型 | 状态 |
|-----------|------|------|
| `sector_alerts` | empty list | 板块预警框架存在但无数据 |
| `follower_signals` | short str | 跟风信号框架存在但结果为空 |
| `f2_data` | short str | F2数据仅含时间戳,无有效数据 |

### 6.2 数据库中有但未被聚合到Dashboard的

| 数据库 | 未聚合表 |
|--------|---------|
| `em_lhb_cache.db` | 6张表全部 — 东方财富龙虎榜增强数据未进bundle |
| `sector_indexes.db` | sector_follower_backtest, sector_leader_stats |
| `chain_engine.db` | stock_chain_v2 — 产业链传导数据 |
| `industry_trend.db` | topic_indices, topic_analysis — 产业链指数 |
| `tetegu_cache.db` | limit_niusan — 牛散标记数据 |
| `holder_cache.db` | holder_backtest — 股东回测数据 |

### 6.3 冗余/废弃数据库 (0字节)

27个空DB文件，包括:
- `limits_v2.db`, `limits_v3.db`, `limit_up_v3.db` (已升级到daily_limit_data)
- `holder_new.db` (已合并到holder_cache)
- `three_funds.db` (三资金数据已通过JSON输出)
- `dashboard.db` (已被bundle取代)
- `auction.db` (竞价数据已通过JSON输出)
- `funds_history.db`, `factors.db`, `lhb_data.db` (已迁移至新版)

### 6.4 数据量不匹配的冗余

- `global_market.db` (28K) vs `macro_cache.db` (84K): 两者内容重叠, global_market更旧
- `astock.db` (4.5M) vs `astock_new.db` (4.1M): 双K线库, 内容可能重复
- `kline_cache.db` (268M) vs 两个astock K线库: 更完整的数据在kline_cache

---

## 七、总结

### 数据规模
- **34个活跃SQLite数据库**, 总计约 **2.1GB**
- **~200+个Python采集/分析脚本**
- **50+个外部API接口** (6大平台: 腾讯/东方财富/同花顺/新浪/华尔街见闻/韭研)
- **3条cron主任务** (每5分钟时间片 + 日终整合)
- **Dashboard Bundle 51个数据键**

### 核心发现
1. **腾讯行情API是基石**: ~40个脚本依赖, 覆盖实时行情/封单/竞价/美股/商品
2. **东方财富API是深度数据源**: 板块/资金流/龙虎榜/涨停/公告
3. **数据重复存储严重**: 27个空DB未清理, kline数据存于4个DB中
4. **板块数据是最大数据量**: `sector_indexes.db` 939M (45%总数据量)
5. **聚合链完备采集→计算→Bundle→SSR→HTTP服务**
