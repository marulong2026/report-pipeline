"""
e05_technical.py — 晚报·技术面模块

复用 M05 逻辑，增加前日对比（变化方向）。
"""

import logging
from datetime import date, datetime
from collections import defaultdict

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e05_technical")


class EveningTechnicalModule(BaseModule):
    """晚报·技术面模块"""

    module_name = "eve_technical"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            prev_date = self.get_prev_trade_date(conn, latest_date)

            holdings = conn.execute(
                "SELECT DISTINCT ts_code, name FROM portfolio WHERE is_active = TRUE AND sold_date IS NULL"
            ).fetchall()

            if not holdings:
                return self._empty_result("无 active 持仓")

            stocks = []
            missing = []

            for code, name in holdings:
                stock = self._analyze(conn, code, name, latest_date, prev_date)
                if stock:
                    stocks.append(stock)
                else:
                    missing.append(code)

        except Exception as e:
            return {
                "stocks": [],
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        status = "complete" if not missing else "partial"

        return {
            "trade_date": latest_date.isoformat(),
            "stocks": stocks,
            "summary": self._build_summary(stocks),
            "data_quality": {
                "status": status,
                "stock_count": len(stocks),
                "missing": missing,
            },
        }

    def _analyze(self, conn, code, name, trade_date, prev_date):
        tr = conn.execute(
            """SELECT ma5, ma10, ma20, ma60, dif, dea, macd
               FROM indicators_trend WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        prev_tr = None
        prev_osc = None
        prev_vol = None
        if prev_date:
            prev_tr = conn.execute(
                """SELECT ma5, ma10, ma20, ma60, dif, dea, macd
                   FROM indicators_trend WHERE ts_code = ? AND trade_date = ?""",
                [code, prev_date],
            ).fetchone()

        osc = conn.execute(
            """SELECT k_value, d_value, j_value, rsi6, rsi12, rsi24, cci
               FROM indicators_oscillator WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        vol = conn.execute(
            """SELECT volume_ratio, turnover_rate
               FROM indicators_volume WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        if not tr:
            return None

        # 均线排列
        alignment = self._judge_ma(tr)
        prev_alignment = self._judge_ma(prev_tr) if prev_tr else None

        # MACD
        macd = self._judge_macd(tr, prev_tr)

        # 变化方向
        trend_change = self._get_trend_change(alignment, prev_alignment, tr, prev_tr)

        return {
            "code": code,
            "name": name,
            "ma_alignment": alignment,
            "prev_ma_alignment": prev_alignment,
            "trend_change": trend_change,
            "macd": macd,
            "oscillator": {
                "kdj_j": float(osc[3]) if osc and osc[3] else None,
                "rsi6": float(osc[4]) if osc and osc[4] else None,
                "cci": float(osc[6]) if osc and osc[6] else None,
            } if osc else None,
            "volume": {
                "volume_ratio": float(vol[0]) if vol and vol[0] else None,
            } if vol else None,
        }

    def _judge_ma(self, tr):
        if not tr:
            return None
        ma5 = float(tr[0]) if tr[0] else None
        ma10 = float(tr[1]) if tr[1] else None
        ma20 = float(tr[2]) if tr[2] else None
        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                return "多头排列"
            if ma5 < ma10 < ma20:
                return "空头排列"
        return "震荡排列"

    def _judge_macd(self, tr, prev_tr):
        dif = float(tr[4]) if tr[4] else None
        dea = float(tr[5]) if tr[5] else None
        macd = float(tr[6]) if tr[6] else None
        if dif is None or dea is None:
            return {"status": "数据不足", "dif": dif, "dea": dea, "macd": macd}

        if prev_tr:
            pdif = float(prev_tr[4]) if prev_tr[4] else None
            pdea = float(prev_tr[5]) if prev_tr[5] else None
            if pdif is not None and pdea is not None:
                if dif > dea and pdif <= pdea:
                    return {"status": "金叉形成", "dif": dif, "dea": dea, "macd": macd}
                if dif < dea and pdif >= pdea:
                    return {"status": "死叉形成", "dif": dif, "dea": dea, "macd": macd}

        if dif > dea:
            return {"status": "金叉区域", "dif": dif, "dea": dea, "macd": macd}
        return {"status": "死叉区域", "dif": dif, "dea": dea, "macd": macd}

    def _get_trend_change(self, cur, prev, cur_tr, prev_tr):
        """判断趋势变化方向。"""
        changes = []
        if prev != cur:
            changes.append(f"均线: {prev}→{cur}")
        if cur_tr and prev_tr:
            cd = float(cur_tr[4]) if cur_tr[4] else 0
            pd = float(prev_tr[4]) if prev_tr[4] else 0
            if cd > pd:
                changes.append("DIF上行")
            elif cd < pd:
                changes.append("DIF下行")
        return " | ".join(changes) if changes else "无明显变化"

    def _build_summary(self, stocks):
        bullish = sum(1 for s in stocks if s["ma_alignment"] == "多头排列")
        bearish = sum(1 for s in stocks if s["ma_alignment"] == "空头排列")
        golden = sum(1 for s in stocks if s["macd"]["status"] in ("金叉形成", "金叉区域"))
        death = sum(1 for s in stocks if s["macd"]["status"] in ("死叉形成", "死叉区域"))
        improving = sum(1 for s in stocks if "DIF上行" in s.get("trend_change", ""))
        worsening = sum(1 for s in stocks if "DIF下行" in s.get("trend_change", ""))
        return {
            "total": len(stocks),
            "bullish": bullish,
            "bearish": bearish,
            "macd_golden": golden,
            "macd_death": death,
            "dif_improving": improving,
            "dif_worsening": worsening,
        }

    def _empty_result(self, reason):
        return {"stocks": [], "data_quality": {"status": "all_failed", "error": reason}}


module = EveningTechnicalModule()
