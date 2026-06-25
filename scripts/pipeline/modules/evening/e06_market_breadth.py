"""
e06_market_breadth.py — 晚报·市场广度模块

数据源（全部从 DB 计算，不依赖 web_search）：
  1. 市场广度：涨跌家数、涨停跌停、成交额（daily_quotes）
  2. 技术广度：站上 MA20 / MA60 比例、MACD 金叉占比（indicators_trend）
  3. 分板块统计（stock_info + daily_quotes）
"""

import logging
from datetime import date, datetime

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e06_breadth")


class MarketBreadthModule(BaseModule):
    """晚报·市场广度模块"""

    module_name = "eve_breadth"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            # 1. 站上均线比例（来自 indicators_trend）
            breadth_indicators = self._calc_breadth_from_db(conn, latest_date)

            # 2. 涨跌家数/涨停跌停/成交额（全部来自 daily_quotes）
            market_stats = self._calc_market_stats(conn, latest_date)

        except Exception as e:
            logger.error(f"市场广度模块异常: {e}", exc_info=True)
            return {
                "breadth_indicators": {},
                "market_stats": {},
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        status = "complete"
        notes = []
        if not breadth_indicators.get("total", 0):
            notes.append("技术指标数据库无数据")
            status = "partial"
        if not market_stats.get("total", 0):
            notes.append("行情数据无数据")
            status = "all_failed"

        return {
            "trade_date": latest_date.isoformat(),
            "breadth_indicators": breadth_indicators,
            "market_stats": market_stats,
            "data_quality": {
                "status": status,
                "notes": notes,
            },
        }

    # ── 技术广度（均线、MACD） ──

    def _calc_breadth_from_db(self, conn, trade_date):
        """从 indicators_trend 表计算市场广度指标（均线、MACD）。"""
        try:
            rows = conn.execute(
                """SELECT
                       ts_code,
                       close,
                       ma20,
                       ma60,
                       dif,
                       dea
                   FROM indicators_trend t
                   JOIN daily_quotes q USING (ts_code, trade_date)
                   WHERE t.trade_date = ?
                   AND t.ma20 IS NOT NULL""",
                [trade_date],
            ).fetchall()

            if not rows:
                return {}

            total = len(rows)
            above_ma20 = 0
            above_ma60 = 0
            macd_golden = 0

            for r in rows:
                close = float(r[1])
                ma20 = float(r[2])
                ma60 = float(r[3]) if r[3] else None
                dif = float(r[4]) if r[4] else None
                dea = float(r[5]) if r[5] else None

                if close >= ma20:
                    above_ma20 += 1
                if ma60 and close >= ma60:
                    above_ma60 += 1
                if dif is not None and dea is not None and dif > dea:
                    macd_golden += 1

            return {
                "total": total,
                "above_ma20": above_ma20,
                "above_ma20_pct": round(above_ma20 / total * 100, 1),
                "above_ma60": above_ma60,
                "above_ma60_pct": round(above_ma60 / total * 100, 1),
                "macd_golden": macd_golden,
                "macd_golden_pct": round(macd_golden / total * 100, 1),
            }
        except Exception as e:
            logger.warning(f"技术广度计算异常: {e}")
            return {}

    # ── 市场统计（涨跌家数、涨停跌停、成交额）全部来自 DB ──

    def _calc_market_stats(self, conn, trade_date):
        """
        从 daily_quotes 表计算市场核心统计。

        使用 self-join 获取每个股票的前一交易日收盘价作为基准，
        计算：涨跌家数、涨停跌停、成交额，并按交易所分类。
        """
        try:
            prev_date = self.get_prev_trade_date(conn, trade_date)
            if prev_date is None:
                return {"total": 0, "error": "无前一交易日数据"}

            # ── 基础统计（全市场） ──
            # 用前收盘价做对比（比用开盘价更准确）
            base = conn.execute(
                """WITH prev AS (
                       SELECT ts_code, CAST(close AS DOUBLE) as prev_close
                       FROM daily_quotes
                       WHERE trade_date = ?
                   )
                   SELECT
                       COUNT(*)                                         AS total,
                       SUM(CAST(q.amount AS DOUBLE))                    AS total_amount,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) > p.prev_close
                                THEN 1 ELSE 0 END)                      AS up_stocks,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) < p.prev_close
                                THEN 1 ELSE 0 END)                      AS down_stocks,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) = p.prev_close
                                THEN 1 ELSE 0 END)                      AS flat_stocks,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) >= p.prev_close * 1.095
                                     AND p.prev_close > 0
                                THEN 1 ELSE 0 END)                      AS limit_up,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) <= p.prev_close * 0.905
                                     AND p.prev_close > 0
                                THEN 1 ELSE 0 END)                      AS limit_down
                   FROM daily_quotes q
                   JOIN prev p ON q.ts_code = p.ts_code
                   WHERE q.trade_date = ?""",
                [prev_date, trade_date],
            ).fetchone()

            if not base or base[0] == 0:
                return {"total": 0, "error": "行情数据为空"}

            total = int(base[0])
            total_amount = float(base[1]) if base[1] else 0.0
            up_stocks = int(base[2])
            down_stocks = int(base[3])
            flat_stocks = int(base[4])
            limit_up = int(base[5])
            limit_down = int(base[6])

            # ── 按交易所分类统计 ──
            # 用 stock_info 表的 exchange 字段做准确分类
            board_stats = conn.execute(
                """WITH prev AS (
                       SELECT ts_code, CAST(close AS DOUBLE) as prev_close
                       FROM daily_quotes
                       WHERE trade_date = ?
                   )
                   SELECT
                       CASE
                           WHEN s.exchange = 'SSE' AND s.market = '主板'   THEN '上证主板'
                           WHEN s.exchange = 'SSE' AND s.market = '科创板'  THEN '科创板'
                           WHEN s.exchange = 'SSE' AND s.market = 'ETF'    THEN '上证ETF'
                           WHEN s.exchange = 'SZSE' AND s.market = '主板'  THEN '深证主板'
                           WHEN s.exchange = 'SZSE' AND s.market = '创业板' THEN '创业板'
                           WHEN s.exchange = 'SZSE' AND s.market = 'ETF'   THEN '深证ETF'
                           WHEN s.exchange = 'BSE'                         THEN '北交所'
                           ELSE '其他'
                       END AS board,
                       COUNT(*)                                            AS cnt,
                       SUM(CAST(q.amount AS DOUBLE))/1e8                   AS amount_yi,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) > p.prev_close
                                THEN 1 ELSE 0 END)                         AS up,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) < p.prev_close
                                THEN 1 ELSE 0 END)                         AS down,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) >= p.prev_close * 1.095
                                     AND p.prev_close > 0
                                THEN 1 ELSE 0 END)                         AS limit_up,
                       SUM(CASE WHEN CAST(q.close AS DOUBLE) <= p.prev_close * 0.905
                                     AND p.prev_close > 0
                                THEN 1 ELSE 0 END)                         AS limit_down
                   FROM daily_quotes q
                   JOIN prev p ON q.ts_code = p.ts_code
                   LEFT JOIN stock_info s ON q.ts_code = s.ts_code
                   WHERE q.trade_date = ?
                   GROUP BY board
                   ORDER BY cnt DESC""",
                [prev_date, trade_date],
            ).fetchall()

            boards = []
            for r in board_stats:
                boards.append({
                    "board": r[0],
                    "count": int(r[1]),
                    "amount_yi": round(float(r[2]), 0),
                    "up": int(r[3]),
                    "down": int(r[4]),
                    "limit_up": int(r[5]),
                    "limit_down": int(r[6]),
                })

            return {
                "total": total,
                "up_stocks": up_stocks,
                "down_stocks": down_stocks,
                "flat_stocks": flat_stocks,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "total_amount": total_amount,
                "total_amount_yi": round(total_amount / 1e8, 0),
                "boards": boards,
                "source": "db_daily_quotes",
            }

        except Exception as e:
            logger.warning(f"市场统计计算异常: {e}")
            return {"total": 0, "error": str(e)}

    def _empty_result(self, reason):
        return {
            "breadth_indicators": {},
            "market_stats": {},
            "data_quality": {"status": "all_failed", "error": reason},
        }


module = MarketBreadthModule()
