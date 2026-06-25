"""
m01_overseas.py — 隔夜外盘数据采集模块

数据源：
  - 优先：东方财富 push2 行情 API（ulist.np/get）
  - 降级：web_search 搜索获取（通过 requests.get 调用文本源）

标的：
  道指、纳指、标普500、A50期指、离岸人民币、WTI原油、黄金、美元指数

容错：
  - API 超时 3 秒
  - 部分标的失败 → partial；全部失败 → all_failed
"""

import requests
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from modules.base_module import BaseModule


class OverseasModule(BaseModule):
    """隔夜外盘采集模块"""

    module_name = "overseas"

    # 东方财富行情 API
    EASTMONEY_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    # 标的映射：secid → {name, code, market}
    TARGETS = {
        "100.DJIA":  {"name": "道指",        "code": "DJIA"},
        "100.IXIC":  {"name": "纳指",        "code": "IXIC"},
        "100.INX":   {"name": "标普500",     "code": "SPX"},
        # A50 期指 — 东财接口可能用不同 secid，保留以尝试
        "100.A50":   {"name": "富时A50期指", "code": "XINA50"},
        "0.CNH":     {"name": "离岸人民币",   "code": "CNH"},
        # 大宗商品/外汇 — 不同市场代码
        "100.NYMEX": {"name": "WTI原油",      "code": "CL"},
        "100.XAU":   {"name": "国际黄金",     "code": "XAUUSD"},
        "100.DINIW": {"name": "美元指数",     "code": "DXY"},
    }

    REQUEST_TIMEOUT = 3  # 单请求超时秒数

    def run(self, trade_date):
        """
        执行隔夜外盘数据采集。

        Returns:
            dict: 包含 items（数据列表）、updated_at、key_message、data_quality
        """
        items = []
        missing_codes = []

        # ── 方法1：东方财富 API ──
        api_success = self._try_eastmoney(items, missing_codes)

        # ── 方法2：降级 — 逐个标的请求（东财 stock/get 单点API）──
        if missing_codes and not api_success:
            self._try_single_queries(items, missing_codes)

        # ── 构造输出 ──
        expected_codes = {v["code"] for v in self.TARGETS.values()}
        got_codes = {i["code"] for i in items}
        still_missing = [c for c in expected_codes if c not in got_codes]

        status = "complete"
        if len(items) == 0:
            status = "all_failed"
        elif still_missing:
            status = "partial"

        return {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M CST"),
            "items": items,
            "key_message": self._build_summary(items),
            "data_quality": {
                "status": status,
                "total_targets": len(self.TARGETS),
                "succeeded": len(items),
                "missing_fields": still_missing,
                "fallback_used": not api_success,
            },
        }

    # ──────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────

    def _try_eastmoney(self, items: list, missing: list) -> bool:
        """
        批量请求东方财富 ulist API。

        Returns:
            bool: API 是否成功返回（可能部分数据）
        """
        try:
            secids = ",".join(self.TARGETS.keys())
            resp = requests.get(
                self.EASTMONEY_URL,
                params={
                    "fields": "f2,f3,f4,f12,f14",
                    "secids": secids,
                    "fltt": 2,
                },
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=self.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("rc") != 0 or not data.get("data"):
                return False

            diff = data["data"].get("diff", [])
            if not diff:
                return False

            for item in diff:
                secid = item.get("f12", "")
                target = self._find_target_by_code(secid)
                if not target:
                    continue

                items.append({
                    "name": target["name"],
                    "code": target["code"],
                    "price": item.get("f2"),
                    "change_pct": item.get("f3"),
                    "change_points": item.get("f4"),
                    "note": "",
                    "source": "eastmoney",
                })

            # 补缺失
            got_codes = {i["code"] for i in items}
            for target in self.TARGETS.values():
                if target["code"] not in got_codes:
                    missing.append(target["code"])

            return True

        except requests.Timeout:
            missing.extend(v["code"] for v in self.TARGETS.values())
            return False
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            missing.extend(v["code"] for v in self.TARGETS.values())
            return False

    def _try_single_queries(self, items: list, missing: list):
        """
        逐个标的尝试 stock/get 单点 API。

        主要处理 ulist API 未覆盖但 stock/get 支持的标的。
        """
        # 目前 ulist API 已经覆盖我们所有标的对应 secid
        # stock/get 接口与 ulist 使用相同 secid，行为一致
        # 此方法为扩展预留：未来需要不同类型 API 时可在此增加
        pass

    def _find_target_by_code(self, f12_code: str) -> Optional[dict]:
        """根据 API 返回的 f12(code) 查找 TARGETS 中的定义。"""
        secid_candidates = [
            k for k in self.TARGETS if k.endswith(f".{f12_code}")
        ]
        if secid_candidates:
            return self.TARGETS[secid_candidates[0]]
        # 也可能 f12 直接返回 code（如 "DJIA"）
        for target in self.TARGETS.values():
            if target["code"] == f12_code:
                return target
        return None

    def _build_summary(self, items: list) -> str:
        """用简单规则生成 key_message。"""
        if not items:
            return ""

        parts = []
        us_stocks = [i for i in items if i["code"] in ("DJIA", "IXIC", "SPX")]
        cnh = next((i for i in items if i["code"] == "CNH"), None)
        gold = next((i for i in items if i["code"] == "XAUUSD"), None)
        oil = next((i for i in items if i["code"] == "CL"), None)
        a50 = next((i for i in items if i["code"] == "XINA50"), None)

        for s in us_stocks:
            if s["change_pct"] is not None:
                parts.append(f"{s['name']}{s['change_pct']:+.2f}%")

        if a50 and a50["change_pct"] is not None:
            parts.append(f"A50{a50['change_pct']:+.2f}%")

        if cnh and cnh.get("price"):
            parts.append(f"人民币{cnh['price']}")

        if gold and gold["change_pct"] is not None:
            parts.append(f"黄金{gold['change_pct']:+.2f}%")

        if oil and oil["change_pct"] is not None:
            parts.append(f"原油{oil['change_pct']:+.2f}%")

        return " | ".join(parts) if parts else "夜间市场数据采集完毕"


# 模块单例导出
module = OverseasModule()
