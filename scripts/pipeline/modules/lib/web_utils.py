"""
web_utils.py — 多源 Fallback 数据采集工具

提供多通道（东财 → tushare pro → 新浪）采集函数，各模块可复用。

用法:
    from modules.lib.web_utils import collect_overseas, collect_capital_flow
    data = collect_overseas()
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger("pipeline.web_utils")

# ── Tushare Token ──
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_APIKEY")

# ── 常量 ──
REQUEST_TIMEOUT = 5  # 单请求超时（秒）


# ════════════════════════════════════════════════════════════
#  Token 获取
# ════════════════════════════════════════════════════════════

def get_tushare_token() -> str:
    """获取 Tushare token（兼容 fill_financial_data.py 风格）。"""
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_APIKEY")
    if not token:
        raise ValueError("Tushare token 未设置。请设置环境变量 TUSHARE_TOKEN。")
    return token


def get_tushare_pro():
    """获取 tushare pro_api 实例。"""
    import tushare as ts
    token = get_tushare_token()
    ts.set_token(token)
    return ts.pro_api()


# ════════════════════════════════════════════════════════════
#  指数行情多源采集（东财 → tushare → 新浪）
# ════════════════════════════════════════════════════════════

# 四大指数对应 ts_code
MAJOR_INDEX_MAP = {
    "000001.SH": {"name": "上证指数", "short": "上证"},
    "399001.SZ": {"name": "深证成指", "short": "深证"},
    "399006.SZ": {"name": "创业板指", "short": "创业板"},
    "000688.SH": {"name": "科创50",   "short": "科创50"},
}


def fetch_index_eastmoney(ts_code: str) -> Optional[dict]:
    """
    从东财 API 获取单指数最新行情。

    Args:
        ts_code: 如 "000001.SH"

    Returns:
        dict: {open, high, low, close, volume, amount} 或 None
    """
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": f"1.{ts_code.replace('.SH','').replace('.SZ','')}"
                     if ts_code.endswith(".SH")
                     else f"0.{ts_code.replace('.SH','').replace('.SZ','')}",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f57,f58,f170",
            "fltt": 2,
        }
        # 浙商转债等校正：上证用 1，深证用 0
        if ts_code.startswith("000") or ts_code.startswith("688") or ts_code.startswith("900"):
            params["secid"] = f"1.{ts_code[:6]}"
        elif ts_code.startswith("399") or ts_code.startswith("002") or ts_code.startswith("300") or ts_code.startswith("001"):
            params["secid"] = f"0.{ts_code[:6]}"

        resp = requests.get(url, params=params,
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") != 0 or not data.get("data"):
            return None
        d = data["data"]
        return {
            "open": float(d.get("f44") or 0),
            "high": float(d.get("f45") or 0),
            "low": float(d.get("f46") or 0),
            "close": float(d.get("f43") or 0),
            "pre_close": float(d.get("f47") or 0),
            "volume": int(d.get("f48") or 0),
            "amount": float(d.get("f50") or 0),
            "change_pct": float(d.get("f170") or 0),
            "source": "eastmoney",
        }
    except Exception as e:
        logger.debug(f"fetch_index_eastmoney({ts_code}) failed: {e}")
        return None


def fetch_index_db(ts_code: str, trade_date) -> Optional[dict]:
    """
    从本地 stock.db index_quotes 表获取数据。
    模块调用方自行传入 duckdb 连接，这里只查。
    """
    # 此函数由各模块在已有 db conn 时用 SQL 查，无需额外实现
    return None


def fetch_index_tushare(ts_code: str, trade_date_str: str) -> Optional[dict]:
    """
    从 tushare pro 指数日线接口获取。

    Args:
        ts_code: 如 "000001.SH"
        trade_date_str: "20260624"

    Returns:
        dict: {open, high, low, close, volume, amount} 或 None
    """
    try:
        pro = get_tushare_pro()
        df = pro.index_daily(ts_code=ts_code, trade_date=trade_date_str)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": int(row.get("vol", 0)),
            "amount": float(row.get("amount", 0)),
            "source": "tushare",
        }
    except Exception as e:
        logger.debug(f"fetch_index_tushare({ts_code}) failed: {e}")
        return None


def fetch_index_sina(ts_code: str) -> Optional[dict]:
    """
    从新浪财经 API 获取指数行情。

    Args:
        ts_code: 如 "000001.SH"

    Returns:
        dict: {open, high, low, close, volume, amount} 或 None
    """
    try:
        # 新浪实时行情格式: s_sh000001, s_sz399001
        suffix = ts_code[:6]
        prefix = "sh" if ts_code.endswith(".SH") else "sz"
        url = f"http://hq.sinajs.cn/list={prefix}{suffix}"
        resp = requests.get(url,
                            headers={
                                "User-Agent": "Mozilla/5.0",
                                "Referer": "https://finance.sina.com.cn",
                            },
                            timeout=REQUEST_TIMEOUT)
        resp.encoding = "gbk"
        text = resp.text.strip()
        if not text or "=" not in text:
            return None
        parts = text.split("=")[1].strip().strip('"').split(",")
        if len(parts) < 10:
            return None
        # 新浪格式: name, open, prev_close, price, high, low, ...
        return {
            "open": float(parts[1]) if parts[1] else None,
            "pre_close": float(parts[2]) if parts[2] else None,
            "close": float(parts[3]) if parts[3] else None,
            "high": float(parts[4]) if parts[4] else None,
            "low": float(parts[5]) if parts[5] else None,
            "volume": int(float(parts[8])) if parts[8] else 0,
            "amount": float(parts[9]) if parts[9] else 0,
            "source": "sina",
        }
    except Exception as e:
        logger.debug(f"fetch_index_sina({ts_code}) failed: {e}")
        return None


def collect_index(ts_code: str, trade_date_str: str = "") -> Optional[dict]:
    """
    多源采集单指数行情。
    东财 → tushare pro（仅当天） → 新浪。

    Args:
        ts_code: 如 "000001.SH"
        trade_date_str: tushare 用日期 "20260624"，空则不试

    Returns:
        dict or None
    """
    result = fetch_index_eastmoney(ts_code)
    if result:
        return result

    if trade_date_str:
        result = fetch_index_tushare(ts_code, trade_date_str)
        if result:
            return result

    result = fetch_index_sina(ts_code)
    if result:
        return result

    return None


# ════════════════════════════════════════════════════════════
#  北向资金多源采集
# ════════════════════════════════════════════════════════════

def fetch_north_moneyflow_from_db(conn, trade_date_str: str) -> Optional[float]:
    """
    从本地 stock.db north_moneyflow 表查北向资金。

    Returns:
        float: 北向资金净买入（元），或 None
    """
    try:
        row = conn.execute(
            "SELECT north_money FROM north_moneyflow WHERE trade_date = ?",
            [trade_date_str]
        ).fetchone()
        return float(row[0]) if row else None
    except Exception as e:
        logger.debug(f"fetch_north_moneyflow_from_db failed: {e}")
        return None


def fetch_north_moneyflow_tushare(trade_date_str: str) -> Optional[float]:
    """
    从 tushare pro moneyflow 接口查北向资金。

    Returns:
        float: 北向资金净买入（元），或 None
    """
    try:
        pro = get_tushare_pro()
        # tushare moneyflow 接口: moneyflow_hsgt
        df = pro.moneyflow_hsgt(trade_date=trade_date_str)
        if df is None or df.empty:
            return None
        # 字段: north_net (北向净买入，亿元)
        north_net = float(df.iloc[0].get("north_net", 0))
        return north_net * 1e8  # 转为元
    except Exception as e:
        logger.debug(f"fetch_north_moneyflow_tushare failed: {e}")
        return None


def collect_north_moneyflow(conn, trade_date_str: str) -> Optional[float]:
    """
    多源采集北向资金。
    本地 DB → tushare pro。

    Returns:
        float: 元单位，或 None
    """
    result = fetch_north_moneyflow_from_db(conn, trade_date_str)
    if result is not None:
        return result

    result = fetch_north_moneyflow_tushare(trade_date_str)
    if result is not None:
        return result

    return None
