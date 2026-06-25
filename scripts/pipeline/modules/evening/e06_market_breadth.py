"""
e06_market_breadth.py — 晚报·市场广度模块

数据源：
  - indicators_trend 表：站上 MA20 / MA60 比例、MACD 金叉占比
  - web_search：涨跌家数、涨停跌停、连板高度
"""

import logging
import re
from datetime import date, datetime

import requests

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e06_breadth")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class MarketBreadthModule(BaseModule):
    """晚报·市场广度模块"""

    module_name = "eve_breadth"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            # 1. 站上均线比例
            breadth_indicators = self._calc_breadth_from_db(conn, latest_date)

            # 2. 搜索补充数据
            search_items = self._search_breadth(trade_date)

        except Exception as e:
            return {
                "breadth_indicators": {},
                "search_items": [],
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        status = "complete"
        notes = []
        if not breadth_indicators.get("total", 0):
            notes.append("技术指标数据库无数据")
            status = "partial"

        return {
            "trade_date": latest_date.isoformat(),
            "breadth_indicators": breadth_indicators,
            "search_items": search_items[:10],
            "data_quality": {
                "status": status,
                "notes": notes,
            },
        }

    def _calc_breadth_from_db(self, conn, trade_date):
        """从 indicators_trend 表计算市场广度指标。"""
        try:
            # 取所有有 trend 数据的股票
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
            logger.warning(f"广度计算异常: {e}")
            return {}

    def _search_breadth(self, trade_date) -> list:
        """搜索今日涨跌家数、涨停跌停、连板高度等市场数据。"""
        queries = [
            f"{trade_date.strftime('%m月%d日')} A股 涨跌家数 涨停 跌停",
            f"{trade_date.strftime('%m月%d日')} 连板 高度 涨停板 复盘",
            f"{trade_date.strftime('%m月%d日')} 市场情绪 涨跌比",
        ]

        all_results = []
        for q in queries:
            try:
                items = self._search(q)
                all_results.extend(items)
            except Exception:
                continue

        # 去重
        seen = set()
        unique = []
        for item in all_results:
            key = item.get("title", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique[:10]

    def _search(self, query: str, limit: int = 5) -> list:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": UA},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception:
            return []

        results = []
        pattern_a = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        pattern_snip = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        for i, (url, title) in enumerate(pattern_a):
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            snippet = ""
            if i < len(pattern_snip):
                snippet = re.sub(r'<[^>]+>', '', pattern_snip[i]).strip()
            results.append({
                "title": title_clean,
                "summary": snippet[:200],
                "url": url,
                "source": "duckduckgo",
            })
            if len(results) >= limit:
                break
        return results

    def _empty_result(self, reason):
        return {
            "breadth_indicators": {},
            "search_items": [],
            "data_quality": {"status": "all_failed", "error": reason},
        }


module = MarketBreadthModule()
