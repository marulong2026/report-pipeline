"""
m03_market.py — 前日市场盘面采集模块

数据源：
  - stock.db index_quotes → 四大指数前日OHLCV
  - stock.db daily_quotes → 全市场涨跌家数、涨停/跌停统计
  - stock.db north_moneyflow → 北向资金

输出：
  四大指数OHLCV + 涨跌家数 + 涨停跌停 + 北向资金含 data_quality
"""

import logging
from datetime import date, datetime
from modules.base_module import BaseModule
from modules.lib.web_utils import collect_north_moneyflow

logger = logging.getLogger("pipeline.m03_market")


class MarketModule(BaseModule):
    """前日市场盘面采集模块"""

    module_name = "prev_market"

    # 四大核心指数
    MAJOR_INDEXES = [
        {"ts_code": "000001.SH", "name": "上证指数"},
        {"ts_code": "399001.SZ", "name": "深证成指"},
        {"ts_code": "399006.SZ", "name": "创业板指"},
        {"ts_code": "000688.SH", "name": "科创50"},
    ]

    # 涨停阈值（%）
    LIMIT_UP_THRESHOLD = 9.5
    LIMIT_DOWN_THRESHOLD = -9.5

    def run(self, trade_date):
        """
        执行前日盘面采集。

        Args:
            trade_date: 交易日

        Returns:
            dict: 含 indexes, market_stats, north_money, data_quality
        """
        conn = self.get_db_conn()

        try:
            # 取最近收盘日（实际取前一日）
            latest_trade_date = self.get_latest_trade_date(conn)
            if latest_trade_date is None:
                return self._empty_result("index_quotes 无数据")

            # ── 1. 四大指数 ──
            indexes = []
            index_missing = []
            for idx in self.MAJOR_INDEXES:
                row = conn.execute(
                    """SELECT trade_date, open, high, low, close, volume, amount
                       FROM index_quotes
                       WHERE ts_code = ? AND trade_date = ?""",
                    [idx["ts_code"], latest_trade_date]
                ).fetchone()
                if row:
                    change_pct = None
                    if row[1] and row[4]:
                        # 估算涨跌幅（如需精确可查前日 close）
                        change_pct = round((float(row[4]) / self._get_index_prev_close(conn, idx["ts_code"], latest_trade_date) - 1) * 100, 2) if self._get_index_prev_close(conn, idx["ts_code"], latest_trade_date) else None
                    indexes.append({
                        "ts_code": idx["ts_code"],
                        "name": idx["name"],
                        "open": float(row[1]) if row[1] else None,
                        "high": float(row[2]) if row[2] else None,
                        "low": float(row[3]) if row[3] else None,
                        "close": float(row[4]) if row[4] else None,
                        "volume": int(row[5]) if row[5] else 0,
                        "amount": float(row[6]) if row[6] else 0,
                        "change_pct": change_pct,
                    })
                else:
                    index_missing.append(idx["ts_code"])

            # ── 2. 涨跌家数 ──
            market_stats = self._calc_market_stats(conn, latest_trade_date)

            # ── 3. 北向资金 ──
            north_money = collect_north_moneyflow(conn, latest_trade_date.isoformat())

        except Exception as e:
            return {
                "indexes": [],
                "market_stats": {},
                "north_money": None,
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        status = "complete"
        notes = []
        if index_missing:
            notes.append(f"缺失指数: {index_missing}")
        if market_stats.get("total", 0) == 0:
            notes.append("全市场数据为空")
            status = "partial"
        if north_money is None:
            notes.append("北向资金无数据")

        if notes:
            status = "partial"

        return {
            "trade_date": latest_trade_date.isoformat(),
            "indexes": indexes,
            "market_stats": market_stats,
            "north_money": north_money,
            "data_quality": {
                "status": status,
                "index_count": len(indexes),
                "total_stocks": market_stats.get("total", 0),
                "notes": notes,
            },
        }

    def _get_index_prev_close(self, conn, ts_code, trade_date):
        """获取指数前日收盘价。"""
        row = conn.execute(
            "SELECT close FROM index_quotes WHERE ts_code = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
            [ts_code, trade_date]
        ).fetchone()
        return float(row[0]) if row and row[0] else None

    def _calc_market_stats(self, conn, trade_date):
        """计算全市场涨跌家数、涨停/跌停。"""
        try:
            # 用 subquery 取前日 close，再算涨幅
            rows = conn.execute(
                """
                WITH prev AS (
                    SELECT ts_code, close AS prev_close
                    FROM daily_quotes
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes WHERE trade_date < ?)
                )
                SELECT
                    q.ts_code,
                    q.close,
                    p.prev_close
                FROM daily_quotes q
                LEFT JOIN prev p ON q.ts_code = p.ts_code
                WHERE q.trade_date = ?
                """,
                [trade_date, trade_date]
            ).fetchall()

            if not rows:
                return {}

            total = len(rows)
            up_count = up_limit = down_count = down_limit = 0

            for r in rows:
                close = float(r[1]) if r[1] else None
                prev_close = float(r[2]) if r[2] else None

                if close is not None and prev_close is not None and prev_close > 0:
                    pct = round((close / prev_close - 1) * 100, 2)
                    if pct > 0:
                        up_count += 1
                        if pct >= self.LIMIT_UP_THRESHOLD:
                            up_limit += 1
                    elif pct < 0:
                        down_count += 1
                        if pct <= self.LIMIT_DOWN_THRESHOLD:
                            down_limit += 1

            return {
                "total": total,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": total - up_count - down_count,
                "up_limit": up_limit,
                "down_limit": down_limit,
                "up_down_ratio": round(up_count / down_count, 2) if down_count > 0 else None,
            }
        except Exception as e:
            logger.warning(f"_calc_market_stats 异常: {e}")
            return {}

    def _empty_result(self, reason):
        return {
            "indexes": [],
            "market_stats": {},
            "north_money": None,
            "data_quality": {"status": "all_failed", "error": reason},
        }


# 模块单例导出
module = MarketModule()
