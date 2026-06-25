"""
m05_technical.py — 技术指标采集模块

数据源：
  - stock.db indicators_trend / indicators_oscillator / indicators_volume
  - 查询持仓股技术指标并判读

输出：
  每只持仓股的均线排列（多头/空头/震荡）、MACD金叉死叉判断
"""

import logging
from datetime import date, datetime
from decimal import Decimal

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.m05_technical")


class TechnicalModule(BaseModule):
    """技术指标采集模块"""

    module_name = "technical_indicators"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("daily_quotes 无数据")

            prev_date = self.get_prev_trade_date(conn, latest_date)

            # 获取持仓股
            holdings = conn.execute(
                "SELECT DISTINCT ts_code, name FROM portfolio WHERE is_active = TRUE AND sold_date IS NULL"
            ).fetchall()

            if not holdings:
                return self._empty_result("无 active 持仓")

            stocks = []
            missing = []

            for code, name in holdings:
                stock_data = self._analyze_stock(conn, code, name, latest_date, prev_date)
                if stock_data:
                    stocks.append(stock_data)
                else:
                    missing.append(code)

            status = "complete"
            notes = []
            if missing:
                notes.append(f"无技术指标: {missing}")
                status = "partial"

        except Exception as e:
            return {
                "stocks": [],
                "summary": {},
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        return {
            "trade_date": latest_date.isoformat(),
            "stocks": stocks,
            "summary": self._build_summary(stocks),
            "data_quality": {
                "status": status,
                "stock_count": len(stocks),
                "missing_indicators": missing,
                "notes": notes,
            },
        }

    def _analyze_stock(self, conn, code, name, trade_date, prev_date):
        """分析单只股票的技术指标。"""
        # 均线排列
        trend = conn.execute(
            """SELECT ma5, ma10, ma20, ma60, dif, dea, macd
               FROM indicators_trend
               WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        # 前日 trend（用于 MACD 交叉判断）
        prev_trend = None
        if prev_date:
            prev_trend = conn.execute(
                """SELECT dif, dea FROM indicators_trend
                   WHERE ts_code = ? AND trade_date = ?""",
                [code, prev_date],
            ).fetchone()

        # 振荡器
        osc = conn.execute(
            """SELECT k_value, d_value, j_value, rsi6, rsi12, rsi24, cci
               FROM indicators_oscillator
               WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        # 量价指标
        vol = conn.execute(
            """SELECT obv, volume_ratio, turnover_rate
               FROM indicators_volume
               WHERE ts_code = ? AND trade_date = ?""",
            [code, trade_date],
        ).fetchone()

        if not trend:
            return None

        return {
            "code": code,
            "name": name,
            "ma_alignment": self._judge_ma_alignment(trend),
            "macd_judgment": self._judge_macd(trend, prev_trend),
            "oscillator": {
                "kdj_k": float(osc[0]) if osc and osc[0] else None,
                "kdj_d": float(osc[1]) if osc and osc[1] else None,
                "kdj_j": float(osc[2]) if osc and osc[2] else None,
                "rsi6": float(osc[3]) if osc and osc[3] else None,
                "rsi12": float(osc[4]) if osc and osc[4] else None,
                "rsi24": float(osc[5]) if osc and osc[5] else None,
                "cci": float(osc[6]) if osc and osc[6] else None,
            } if osc else None,
            "volume": {
                "obv": float(vol[0]) if vol and vol[0] else None,
                "volume_ratio": float(vol[1]) if vol and vol[1] else None,
                "turnover_rate": float(vol[2]) if vol and vol[2] else None,
            } if vol else None,
        }

    def _judge_ma_alignment(self, trend):
        """
        判读均线排列。

        Returns:
            str: "牛市排列" | "熊市排列" | "震荡排列"
        """
        if not trend:
            return "数据不足"

        ma5 = float(trend[0]) if trend[0] else None
        ma10 = float(trend[1]) if trend[1] else None
        ma20 = float(trend[2]) if trend[2] else None
        ma60 = float(trend[3]) if trend[3] else None

        mas = [v for v in [ma5, ma10, ma20, ma60] if v is not None]
        if len(mas) < 3:
            return "数据不足"

        # 多头排列: 短 > 长（价格 > 所有均线，且短均线 > 长均线）
        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                if not ma60 or ma20 > ma60:
                    return "多头排列"
        # 空头排列: 短 < 长
        if ma5 and ma10 and ma20:
            if ma5 < ma10 < ma20:
                if not ma60 or ma20 < ma60:
                    return "空头排列"

        return "震荡排列"

    def _judge_macd(self, trend, prev_trend):
        """
        判读 MACD 状态。

        Returns:
            dict: {status, dif, dea, macd}
        """
        if not trend:
            return {"status": "数据不足", "dif": None, "dea": None, "macd": None}

        dif = float(trend[4]) if trend[4] else None
        dea = float(trend[5]) if trend[5] else None
        macd = float(trend[6]) if trend[6] else None

        result = {"dif": dif, "dea": dea, "macd": macd}

        if dif is None or dea is None:
            result["status"] = "数据不足"
            return result

        # 金叉/死叉判断（与前日对比）
        if prev_trend:
            prev_dif = float(prev_trend[0]) if prev_trend[0] else None
            prev_dea = float(prev_trend[1]) if prev_trend[1] else None
            if prev_dif is not None and prev_dea is not None:
                now_cross = dif > dea
                prev_cross = prev_dif > prev_dea
                if now_cross and not prev_cross:
                    result["status"] = "金叉（今日形成）"
                    return result
                if not now_cross and prev_cross:
                    result["status"] = "死叉（今日形成）"
                    return result

        # 常态判断
        if dif > dea:
            result["status"] = "金叉区域"
        elif dif < dea:
            result["status"] = "死叉区域"
        else:
            result["status"] = "粘合"

        return result

    def _build_summary(self, stocks):
        """生成汇总信息。"""
        bullish = sum(1 for s in stocks if s["ma_alignment"] == "多头排列")
        bearish = sum(1 for s in stocks if s["ma_alignment"] == "空头排列")
        oscillating = sum(1 for s in stocks if s["ma_alignment"] == "震荡排列")
        golden = sum(1 for s in stocks if "金叉" in s["macd_judgment"]["status"])
        death = sum(1 for s in stocks if "死叉" in s["macd_judgment"]["status"])

        return {
            "total_stocks": len(stocks),
            "ma_bullish": bullish,
            "ma_bearish": bearish,
            "ma_oscillating": oscillating,
            "macd_golden": golden,
            "macd_death": death,
        }

    def _empty_result(self, reason):
        return {
            "stocks": [],
            "summary": {},
            "data_quality": {"status": "all_failed", "error": reason},
        }


# 模块单例导出
module = TechnicalModule()
