"""
e03_capital_flow.py — 晚报·板块资金流向模块

搜索当日主力资金流入流出 TOP 板块、北向资金。
复用 M06 搜索逻辑。
"""

import logging
import re
from datetime import date, datetime

import requests

from modules.base_module import BaseModule
from modules.lib.web_utils import collect_north_moneyflow

logger = logging.getLogger("pipeline.e03_capital_flow")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class EveningCapitalFlowModule(BaseModule):
    """晚报·板块资金流向模块"""

    module_name = "eve_capital_flow"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            # 北向资金
            north_money = collect_north_moneyflow(conn, latest_date.isoformat())

            # 板块资金流向搜索
            top_inflow = self._search("主力资金流入 TOP 板块 今日")
            top_outflow = self._search("主力资金流出 TOP 板块 今日")
            north_search = self._search(f"{latest_date.strftime('%m月%d日')} 北向资金 净流入")

        except Exception as e:
            return {
                "north_money": None,
                "top_inflow": [],
                "top_outflow": [],
                "north_news": [],
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        notes = []
        if north_money is None:
            notes.append("北向资金无数据")

        status = "complete"
        if notes or (not top_inflow and not top_outflow):
            status = "partial"

        return {
            "trade_date": latest_date.isoformat(),
            "north_money": north_money,
            "north_money_str": f"{north_money / 1e8:.2f}亿" if north_money else None,
            "top_inflow": top_inflow[:5],
            "top_outflow": top_outflow[:5],
            "north_news": north_search[:5],
            "data_quality": {
                "status": status,
                "notes": notes,
            },
        }

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
                "source": "duckduckgo",
            })
            if len(results) >= limit:
                break
        return results

    def _empty_result(self, reason):
        return {
            "north_money": None,
            "top_inflow": [],
            "top_outflow": [],
            "north_news": [],
            "data_quality": {"status": "all_failed", "error": reason},
        }


module = EveningCapitalFlowModule()
