"""
e01_index.py — 晚报·当日指数行情模块

复用 M03 逻辑，取当日四大指数 OHLCV。
"""

import logging
from datetime import date, datetime

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e01_index")

MAJOR_INDEXES = [
    {"ts_code": "000001.SH", "name": "上证指数"},
    {"ts_code": "399001.SZ", "name": "深证成指"},
    {"ts_code": "399006.SZ", "name": "创业板指"},
    {"ts_code": "000688.SH", "name": "科创50"},
]


class EveningIndexModule(BaseModule):
    """晚报·当日指数行情模块"""

    module_name = "eve_index"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            indexes = []
            prev_date = self.get_prev_trade_date(conn, latest_date)

            for idx in MAJOR_INDEXES:
                row = conn.execute(
                    """SELECT trade_date, open, high, low, close, volume, amount
                       FROM index_quotes WHERE ts_code = ? AND trade_date = ?""",
                    [idx["ts_code"], latest_date],
                ).fetchone()
                if not row:
                    indexes.append({
                        "ts_code": idx["ts_code"],
                        "name": idx["name"],
                        "error": "no_data",
                    })
                    continue

                # 涨跌幅计算
                prev_close = None
                if prev_date:
                    pc = conn.execute(
                        "SELECT close FROM index_quotes WHERE ts_code = ? AND trade_date = ?",
                        [idx["ts_code"], prev_date],
                    ).fetchone()
                    prev_close = float(pc[0]) if pc and pc[0] else None

                close = float(row[4]) if row[4] else None
                change_pct = None
                if close and prev_close and prev_close > 0:
                    change_pct = round((close / prev_close - 1) * 100, 2)

                indexes.append({
                    "ts_code": idx["ts_code"],
                    "name": idx["name"],
                    "open": float(row[1]) if row[1] else None,
                    "high": float(row[2]) if row[2] else None,
                    "low": float(row[3]) if row[3] else None,
                    "close": close,
                    "volume": int(row[5]) if row[5] else 0,
                    "amount": float(row[6]) if row[6] else 0,
                    "change_pct": change_pct,
                })

        except Exception as e:
            return {
                "indexes": [],
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        missing = [i["ts_code"] for i in indexes if "error" in i]
        status = "complete" if not missing else "partial"

        return {
            "trade_date": latest_date.isoformat(),
            "indexes": indexes,
            "data_quality": {
                "status": status,
                "index_count": len(indexes),
                "missing_indexes": missing,
            },
        }

    def _empty_result(self, reason):
        return {
            "indexes": [],
            "data_quality": {"status": "all_failed", "error": reason},
        }


# 模块单例导出
module = EveningIndexModule()
