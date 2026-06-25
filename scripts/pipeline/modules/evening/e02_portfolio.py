"""
e02_portfolio.py — 晚报·持仓盈亏模块

复用 M02 逻辑，取当日收盘 + 盈亏计算。
"""

import logging
from datetime import date, datetime
from collections import defaultdict

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e02_portfolio")

PROFIT_ONLY_THRESHOLD = 0


class EveningPortfolioModule(BaseModule):
    """晚报·持仓盈亏模块"""

    module_name = "eve_portfolio"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            prev_date = self.get_prev_trade_date(conn, latest_date)

            # 查 active 持仓
            holdings_raw = conn.execute(
                """SELECT ts_code, name, avg_cost, priority
                   FROM portfolio WHERE is_active = TRUE AND sold_date IS NULL
                   ORDER BY ts_code, priority"""
            ).fetchall()

            if not holdings_raw:
                return self._empty_result("无 active 持仓")

            codes = list(set(r[0] for r in holdings_raw))

            # 当日收盘价与前日收盘价
            close_data = {}
            for code in codes:
                row = conn.execute(
                    "SELECT close FROM daily_quotes WHERE ts_code = ? AND trade_date = ?",
                    [code, latest_date],
                ).fetchone()
                close_data[code] = float(row[0]) if row and row[0] else None

            prev_close_data = {}
            if prev_date:
                for code in codes:
                    row = conn.execute(
                        "SELECT close FROM daily_quotes WHERE ts_code = ? AND trade_date = ?",
                        [code, prev_date],
                    ).fetchone()
                    prev_close_data[code] = float(row[0]) if row and row[0] else None

            # 聚合
            grouped = defaultdict(lambda: {"costs": [], "accounts": set(), "name": ""})
            for code, name, cost, priority in holdings_raw:
                g = grouped[code]
                if cost is not None:
                    g["costs"].append(float(cost))
                g["accounts"].add(priority)
                g["name"] = name or code

            holdings = []
            total_profit = 0
            total_loss = 0
            profit_only_count = 0
            missing_codes = []

            for code, g in sorted(grouped.items()):
                close = close_data.get(code)
                prev_close = prev_close_data.get(code)

                costs = g["costs"]
                has_negative = any(c <= PROFIT_ONLY_THRESHOLD for c in costs)
                is_profit_only = has_negative

                avg_cost = None
                if not is_profit_only and costs:
                    avg_cost = round(sum(costs) / len(costs), 3)

                # 日涨跌幅
                day_change_pct = None
                if close is not None and prev_close is not None and prev_close > 0:
                    day_change_pct = round((close / prev_close - 1) * 100, 2)

                # 累计盈亏
                cum_pct = None
                if avg_cost is not None and avg_cost > 0 and close is not None:
                    cum_pct = round((close / avg_cost - 1) * 100, 2)

                if cum_pct is not None:
                    if cum_pct >= 0:
                        total_profit += 1
                    else:
                        total_loss += 1

                if is_profit_only:
                    profit_only_count += 1

                if close is None:
                    missing_codes.append(code)

                holdings.append({
                    "code": code,
                    "name": g["name"],
                    "close": close,
                    "day_change_pct": day_change_pct,
                    "avg_cost": avg_cost,
                    "cum_pct": cum_pct,
                    "is_profit_only": is_profit_only,
                    "account_ids": sorted(g["accounts"]),
                })

            summary = {
                "total_holdings": len(holdings),
                "total_profit_stocks": total_profit,
                "total_loss_stocks": total_loss,
                "profit_only_stocks": profit_only_count,
                "trade_date": latest_date.isoformat(),
            }

        except Exception as e:
            return {
                "holdings": [],
                "summary": {},
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        status = "complete" if not missing_codes else "partial"

        return {
            "holdings": holdings,
            "summary": summary,
            "data_quality": {
                "status": status,
                "missing_codes": missing_codes,
            },
        }

    def _empty_result(self, reason):
        return {
            "holdings": [],
            "summary": {},
            "data_quality": {"status": "all_failed", "error": reason},
        }


module = EveningPortfolioModule()
