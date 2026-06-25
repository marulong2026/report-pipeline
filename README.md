# Report Pipeline — 工业级报告生成流水线

早报/晚报标准化流程：模块化数据采集 → JSON数据包 → LLM分析 → 多渠道推送

## 快速部署

```bash
# 1. 安装依赖
pip install duckdb pandas requests pyyaml jinja2

# 2. 设置环境变量
export STOCK_DB_PATH=/path/to/stock.db
export TUSHARE_TOKEN=***

# 3. 生成报告
python3 scripts/pipeline/pipeline_orchestrator.py --type morning
python3 scripts/pipeline/pipeline_orchestrator.py --type evening
```

详情见 `skills/report-pipeline.md`
