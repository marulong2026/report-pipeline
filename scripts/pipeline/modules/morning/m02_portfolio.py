"""
m02_portfolio.py — 持仓行情采集模块

数据源：
  - DuckDB stock.db → portfolio + daily_quotes 表

逻辑：
  1. 取 portfolio 表中 is_active=true 且 sold_date IS NULL 的全部持仓
  2. JOIN daily_quotes 取最新收盘价和前一日收盘价
  3. 按 ts_code 分组，计算成本偏离幅度
  4. 处理 588000.SH avg_cost 为负数的纯利润仓标记

注意：
  - priority 字段用作 account_id（1=主仓, 2=副仓）
  - 水晶光电 002273.SZ 可能需要标记首日涨跌为 null
  - 全部使用 read_only 连接
"""

from datetime import date, datetime
from collections import defaultdict
from modules.base_module import BaseModule


class PortfolioModule(BaseModule):
    """持仓行情采集模块"""

    module_name = "portfolio"

    # 纯利润仓标记阈值：avg_cost <= 0 时视为利润仓
    PROFIT_ONLY_THRESHOLD = 0

    def run(self, trade_date):
        """
        执行持仓行情采集。

        Args:
            trade_date: 交易日（实际取最新 complete 数据日）

        Returns:
            dict: 包含 holdings、summary、data_quality
        """
        conn = self.get_db_conn()

        try:
            # 1. 获取实际交易日（latest 完整收盘日）
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("daily_quotes 无数据")

            prev_date = self.get_prev_trade_date(conn, latest_date)

            # 2. 查询 active 持仓
            holdings_raw = conn.execute("""
                SELECT ts_code, name, avg_cost, priority
                FROM portfolio
                WHERE is_active = TRUE AND sold_date IS NULL
                ORDER BY ts_code, priority
            """).fetchall()

            if not holdings_raw:
                return self._empty_result("无 active 持仓")

            # 3. 获取全部持仓代码的最新收盘价
            codes = list(set(r[0] for r in holdings_raw))
            placeholders = ",".join("?" for _ in codes)
            close_data = {}
            for code in codes:
                row = conn.execute("""
                    SELECT close FROM daily_quotes
                    WHERE ts_code = ? AND trade_date = ?
                """, [code, latest_date]).fetchone()
                close_data[code] = float(row[0]) if row and row[0] else None

            # 前日收盘
            prev_close_data = {}
            for code in codes:
                if prev_date:
                    row = conn.execute("""
                        SELECT close FROM daily_quotes
                        WHERE ts_code = ? AND trade_date = ?
                    """, [code, prev_date]).fetchone()
                    prev_close_data[code] = float(row[0]) if row and row[0] else None
                else:
                    prev_close_data[code] = None

            # 4. 按 ts_code 聚合
            grouped = defaultdict(lambda: {
                "costs": [], "accounts": set(), "name": ""
            })
            for code, name, cost, priority in holdings_raw:
                g = grouped[code]
                if cost is not None:
                    g["costs"].append(float(cost))
                g["accounts"].add(priority)
                g["name"] = name or code

            # 5. 构建 holdings 数组
            holdings = []
            missing_codes = []
            notes = []
            total_profit = 0
            total_loss = 0
            profit_only_count = 0

            for code, g in sorted(grouped.items()):
                close = close_data.get(code)
                prev_close = prev_close_data.get(code)

                costs = g["costs"]
                has_negative = any(c <= self.PROFIT_ONLY_THRESHOLD for c in costs)
                positive_costs = [c for c in costs if c > self.PROFIT_ONLY_THRESHOLD]

                is_profit_only = has_negative
                avg_cost = None
                cum_pct = None
                deviation_pct = None

                if is_profit_only:
                    # 纯利润仓：所有成本/偏离字段标记为 null
                    profit_only_count += 1
                else:
                    if costs:
                        avg_cost = round(sum(costs) / len(costs), 3)

                # 日涨跌幅
                day_change_pct = None
                if close is not None and prev_close is not None and prev_close > 0:
                    day_change_pct = round((close / prev_close - 1) * 100, 2)

                # 累计盈亏 / 成本偏离（同值）
                if avg_cost is not None and avg_cost > 0 and close is not None:
                    cum_pct = round((close / avg_cost - 1) * 100, 2)
                    deviation_pct = cum_pct

                if cum_pct is not None:
                    if cum_pct >= 0:
                        total_profit += 1
                    else:
                        total_loss += 1

                holding = {
                    "code": code,
                    "name": g["name"],
                    "close": close,
                    "day_change_pct": day_change_pct,
                    "avg_cost": avg_cost,
                    "cost_details": [round(c, 3) for c in costs],
                    "cum_pct": cum_pct,
                    "deviation_pct": deviation_pct,
                    "is_profit_only": is_profit_only,
                    "account_ids": sorted(g["accounts"]),
                }
                holdings.append(holding)

                if close is None:
                    missing_codes.append(code)
                    notes.append(f"{code} 无最新收盘价")

            # 6. 构建 summary
            summary = {
                "total_holdings": len(holdings),
                "total_profit_stocks": total_profit,
                "total_loss_stocks": total_loss,
                "profit_only_stocks": profit_only_count,
                "trade_date": latest_date.isoformat(),
                "data_period": f"{prev_date.isoformat() if prev_date else 'N/A'} ~ {latest_date.isoformat()}",
            }

        except Exception as e:
            return {
                "holdings": [],
                "summary": {},
                "data_quality": {
                    "status": "all_failed",
                    "error": str(e),
                },
            }
        finally:
            conn.close()

        # 7. 数据质量
        status = "complete"
        if missing_codes:
            status = "partial"

        return {
            "holdings": holdings,
            "summary": summary,
            "data_quality": {
                "status": status,
                "missing_codes": missing_codes,
                "notes": notes,
            },
        }

    def _empty_result(self, reason: str) -> dict:
        """空结果模板。"""
        return {
            "holdings": [],
            "summary": {},
            "data_quality": {
                "status": "all_failed",
                "error": reason,
            },
        }


# 模块单例导出
module = PortfolioModule()
