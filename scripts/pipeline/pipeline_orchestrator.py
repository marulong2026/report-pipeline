"""
pipeline_orchestrator.py — 数据采集流水线调度器

用法：
  python3 pipeline_orchestrator.py --type morning
  python3 pipeline_orchestrator.py --type morning --date 2026-06-24

流程：
  1. 检查交易日（trading_calendar 表）
  2. 依次加载执行各模块
  3. 合并 → JSON 数据包
  4. 保存到 /mnt/d/stock-data/reports/data_packets/{type}_{date}.json
"""

import argparse
import importlib
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

# ── 路径常量 ──
DB_PATH = "/mnt/d/stock-data/stock.db"
BASE_DIR = Path("/home/pogu/.openclaw/workspace/scripts/pipeline")
OUTPUT_DIR = Path("/mnt/d/stock-data/reports")
DATA_PACKET_DIR = OUTPUT_DIR / "data_packets"

# ── 日志 ──
LOG_DIR = OUTPUT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("pipeline")

# ── 模块映射 ──
# (section_name, module_import_path)
MORNING_MODULES = [
    ("overseas",            "modules.morning.m01_overseas"),
    ("portfolio",           "modules.morning.m02_portfolio"),
    ("prev_market",         "modules.morning.m03_market"),
    ("event_calendar",      "modules.morning.m04_calendar"),
    ("technical_indicators", "modules.morning.m05_technical"),
    ("capital_flow",        "modules.morning.m06_capital_flow"),
]

EVENING_MODULES = [
    ("eve_index",         "modules.evening.e01_index"),
    ("eve_portfolio",     "modules.evening.e02_portfolio"),
    ("eve_capital_flow",  "modules.evening.e03_capital_flow"),
    ("eve_news",          "modules.evening.e04_news"),
    ("eve_technical",     "modules.evening.e05_technical"),
    ("eve_breadth",       "modules.evening.e06_market_breadth"),
]


def is_trading_day(target_date: date) -> bool:
    """查询 trading_calendar 确认是否为交易日。"""
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        row = conn.execute(
            "SELECT 1 FROM trading_calendar WHERE date = ? AND is_trading_day = TRUE",
            [target_date]
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def load_module(module_path: str):
    """
    动态加载模块实例。
    模块文件需暴露 'module' 变量（BaseModule 实例）。
    """
    try:
        mod = importlib.import_module(module_path)
        instance = getattr(mod, "module", None)
        if instance is None:
            raise ImportError(f"{module_path} 未导出 'module' 变量")
        print(f"  ✓ {instance}")  # 用于终端友好输出
        return instance
    except Exception as e:
        logger.error(f"加载模块失败 {module_path}: {e}")
        raise


def build_data_packet(report_type: str, trade_date: date) -> dict:
    """
    依次执行所有模块，合并为统一 JSON 数据包。

    Args:
        report_type: "morning" | "evening"
        trade_date:  交易日

    Returns:
        dict: 完整数据包
    """
    modules = MORNING_MODULES if report_type == "morning" else EVENING_MODULES

    if not modules:
        logger.warning(f"{report_type} 模块列表为空，跳过执行")
        data_packet["meta"]["module_count"] = 0
        return data_packet

    data_packet = {
        "meta": {
            "report_type": report_type,
            "trade_date": trade_date.isoformat(),
            "generated_at": datetime.now().isoformat(),
            "version": "1.0",
            "module_count": len(modules),
        },
        "sections": {},
        "data_quality": {
            "total_modules": len(modules),
            "succeeded": 0,
            "failed": 0,
            "partial": 0,
            "details": {},
        },
    }

    logger.info(f"开始执行 {report_type} 数据采集（{trade_date}），共 {len(modules)} 个模块")

    for section_name, module_path in modules:
        logger.info(f"  → 模块 [{section_name}] 加载中...")
        try:
            module = load_module(module_path)
            logger.info(f"  → 模块 [{section_name}] 开始执行...")
            result = module.run(trade_date)
            data_packet["sections"][section_name] = result

            quality = result.get("data_quality", {})
            status = quality.get("status", "unknown")

            if status == "complete":
                data_packet["data_quality"]["succeeded"] += 1
            elif status == "all_failed":
                data_packet["data_quality"]["failed"] += 1
            else:
                data_packet["data_quality"]["partial"] += 1

            data_packet["data_quality"]["details"][section_name] = status
            logger.info(f"  ✓ 模块 [{section_name}] 完成（{status}）")

        except Exception as e:
            data_packet["sections"][section_name] = None
            data_packet["data_quality"]["failed"] += 1
            data_packet["data_quality"]["details"][section_name] = f"error: {e}"
            logger.error(f"  ✗ 模块 [{section_name}] 失败: {e}")

    logger.info(
        f"采集完成：成功 {data_packet['data_quality']['succeeded']} / "
        f"部分 {data_packet['data_quality']['partial']} / "
        f"失败 {data_packet['data_quality']['failed']}"
    )

    return data_packet


def save_data_packet(data_packet: dict, report_type: str, trade_date: date) -> Path:
    """保存数据包到 JSON 文件。"""
    DATA_PACKET_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{report_type}_{trade_date.isoformat()}.json"
    filepath = DATA_PACKET_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data_packet, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ 数据包已保存: {filepath}")
    return filepath


def main():
    ap = argparse.ArgumentParser(description="早报/晚报数据采集流水线调度器")
    ap.add_argument("--type", required=True, choices=["morning", "evening"],
                    help="采集类型：morning（早报）/ evening（晚报）")
    ap.add_argument("--date", default=None,
                    help="交易日（YYYY-MM-DD），默认当天。用于测试时可指定历史日期")
    ap.add_argument("--force", action="store_true",
                    help="强制运行（跳过交易日检查）")
    args = ap.parse_args()

    trade_date = date.fromisoformat(args.date) if args.date else date.today()

    print(f"\n{'='*60}")
    print(f"  数据采集流水线 — {args.type.upper()}")
    print(f"  交易日: {trade_date.isoformat()}")
    print(f"{'='*60}\n")

    # 交易日检查
    if not args.force and not is_trading_day(trade_date):
        print(f"⚠️  {trade_date} 非交易日，跳过执行。")
        print(f"   如需强制运行，请加 --force 参数。")
        return

    # 执行采集
    data_packet = build_data_packet(args.type, trade_date)

    # 保存数据包
    packet_path = save_data_packet(data_packet, args.type, trade_date)

    # 输出摘要
    print(f"\n{'='*60}")
    print(f"  ✅ 数据采集完成")
    print(f"  数据包: {packet_path}")
    print(f"  模块概况:")
    dq = data_packet["data_quality"]
    print(f"    ✓ 成功: {dq['succeeded']}")
    print(f"    ~ 部分: {dq['partial']}")
    print(f"    ✗ 失败: {dq['failed']}")
    for name, status in dq["details"].items():
        emoji = {"complete": "✓", "partial": "~", "all_failed": "✗"}.get(status, "?")
        print(f"      {emoji} {name}: {status}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
