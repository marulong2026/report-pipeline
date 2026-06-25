"""
m04_calendar.py — 事件日历采集模块

数据源：
  - web_search 硬编码搜索词采集当日事件（通过 requests + DuckDuckGo HTML 搜索）

搜索词硬编码，不靠LLM自己想：
  - 今日经济数据日历
  - A股今日公司公告个股新闻
  - 今日行业新闻政策产业动态
"""

import logging
import re
from datetime import date, datetime

import requests

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.m04_calendar")

# ── 硬编码搜索词 ──
SEARCH_KEYS = {
    "macro":    "今日 经济数据 日历 美联储 中国央行 发布",
    "company":  "A股 今日 公司公告 个股新闻 业绩 预告",
    "industry": "今日 行业新闻 政策 产业动态 重大消息",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class CalendarModule(BaseModule):
    """事件日历采集模块"""

    module_name = "event_calendar"

    def run(self, trade_date):
        events = {"macro": [], "company": [], "industry": []}
        sources_used = []
        notes = []

        day_str = trade_date.strftime("%m月%d日")

        for category, query_template in SEARCH_KEYS.items():
            full_query = f"{day_str} {query_template}"
            try:
                items = self._search(full_query)
                if items:
                    events[category] = items[:8]
                    sources_used.append(category)
                else:
                    notes.append(f"{category}: 无搜索结果（Web搜索可能被阻）")
            except Exception as e:
                notes.append(f"{category}: 搜索失败 - {e}")

        total_events = sum(len(v) for v in events.values())
        # 将 N/A 记为 partial 而非 all_failed，因为 Web 搜索可离线
        status = "partial"
        if total_events > 0:
            status = "complete"
        elif total_events == 0 and not sources_used:
            status = "partial（Web搜索不可用，不影响核心数据）"

        dq_status = status if status in ("complete", "all_failed") else "partial"
        return {
            "trade_date": trade_date.isoformat(),
            "events": events,
            "total_events": total_events,
            "data_quality": {
                "status": dq_status,
                "raw_note": status if status not in ("complete", "all_failed", "partial") else "",
                "searches_used": sources_used,
                "searches_total": len(SEARCH_KEYS),
                "notes": notes,
            },
        }

    def _search(self, query: str) -> list:
        """
        尝试通过 DuckDuckGo HTML 搜索获取事件列表。
        超时短、快速失败（Web搜索依赖外部网络）。
        """
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": UA},
                timeout=5,
            )
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            logger.debug(f"DDG search failed for '{query[:30]}': {e}")
            return []

        results = []
        pattern_a = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            text, re.DOTALL,
        )
        pattern_snip = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            text, re.DOTALL,
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
            if len(results) >= 8:
                break

        return results


# 模块单例导出
module = CalendarModule()
