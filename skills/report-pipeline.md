# Report Pipeline — 工业级报告生成流水线

## 架构

数据采集层(代码) → JSON数据包 → LLM分析层(只填充+判断) → 多渠道推送

## 模块

### 早报 (6个)
M01 隔夜外盘 | M02 持仓行情 | M03 前日盘面 | M04 事件日历 | M05 技术指标 | M06 资金流向

### 晚报 (6个)
E01 当日指数 | E02 持仓盈亏 | E03 板块资金 | E04 新闻联播 | E05 技术面 | E06 市场广度

## 使用

```bash
python3 scripts/pipeline/pipeline_orchestrator.py --type morning --date 2026-06-25
python3 scripts/pipeline/pipeline_orchestrator.py --type evening --date 2026-06-24
```

## 配置

环境变量: STOCK_DB_PATH, TUSHARE_TOKEN, FEISHU_USER_ID
