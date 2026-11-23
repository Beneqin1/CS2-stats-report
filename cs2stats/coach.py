"""Actionable coaching advice engine.

Each rule is a small callable over a pre-built context and returns a card
dict (or None when the trigger threshold is not met).  Rules are plain
functions in the RULES list so they can be externalised to JSON/YAML later
without touching the engine.  Every card anchors to the map and to concrete
numbers, and carries the evidence used to fire it.
"""

from __future__ import annotations

from typing import Any, Callable

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

Rule = Callable[[dict[str, Any]], dict[str, Any] | None]


def _card(rule_id: str, severity: str, title: str, body: str,
          practice: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule_id,
        "severity": severity,
        "title": title,
        "body": body,
        "practice": practice,
        "evidence": evidence,
    }


def rule_fk_rate_low(ctx: dict[str, Any]) -> dict[str, Any] | None:
    fk = ctx["opening_kills"] + ctx["opening_deaths"]
    if fk < 5:
        return None
    rate = ctx["opening_kills"] / fk
    if rate >= 0.40:
        return None
    return _card(
        "fk_rate_low", "high", "首杀对决胜率偏低",
        f"{ctx['map']}：你参与 {fk} 次首杀对决，胜率仅 {rate:.0%}（健康线 50%）。"
        "首杀送掉会让队伍整局 4v5。",
        "死斗/预瞄图专项：每天 15 分钟预瞄练习，开局首杀尝试前先确认掩体与撤退路线。",
        {"opening_kills": ctx["opening_kills"], "opening_deaths": ctx["opening_deaths"],
         "fk_rate": round(rate, 3)},
    )


def rule_fk_attempt_low(ctx: dict[str, Any]) -> dict[str, Any] | None:
    rounds = ctx["rounds"] or 1
    attempts = ctx["opening_kills"] + ctx["opening_deaths"]
    rate = attempts / rounds
    if attempts < 3 or rate >= 0.15:
        return None
    return _card(
        "fk_attempt_low", "low", "首杀参与过少",
        f"{ctx['map']}：{rounds} 回合里你只参与了 {attempts} 次首杀对决（{rate:.0%}/回合）。"
        "长期不参与首杀，队伍开局信息与人头交换都靠队友。",
        "每半场选 1-2 个固定开局点位主动要枪位（如 Mirage 中路 VIP / A1 前顶），配合队友闪光。",
        {"opening_attempts": attempts, "rounds": rounds, "attempt_rate": round(rate, 3)},
    )


def rule_pistol_weak(ctx: dict[str, Any]) -> dict[str, Any] | None:
    pistol_kills = ctx["pistol_round_kills"]
    pistol_rounds = ctx["pistol_rounds"]
    overall_kpr = ctx["kpr"]
    if not pistol_rounds or overall_kpr <= 0:
        return None
    pistol_kpr = pistol_kills / pistol_rounds
    if pistol_kpr >= 0.6 * overall_kpr:
        return None
    return _card(
        "pistol_weak", "medium", "手枪局产出偏低",
        f"{ctx['map']}：手枪局（R1/R13）你 {pistol_rounds} 局 {pistol_kills} 杀"
        f"（{pistol_kpr:.2f} KPR），只有全场 KPR（{overall_kpr:.2f}）的 "
        f"{(pistol_kpr / overall_kpr):.0%}。",
        "每天 10 分钟 aim_botz 手枪模式，专练 USP/P2000 爆头线；手枪局跟队抱团补枪而非单摸。",
        {"pistol_rounds": pistol_rounds, "pistol_kills": pistol_kills,
         "pistol_kpr": round(pistol_kpr, 3), "overall_kpr": round(overall_kpr, 3)},
    )


def rule_hs_low(ctx: dict[str, Any]) -> dict[str, Any] | None:
    if ctx["kills"] < 10 or ctx["hs_pct"] >= 45.0:
        return None
    return _card(
        "hs_low", "medium", "爆头率偏低",
        f"{ctx['map']}：爆头率 {ctx['hs_pct']:.0f}%（{ctx['hs_kills']}/{ctx['kills']}），"
        "低于 45% 的常见基准，说明预瞄高度或急停质量不稳定。",
        "死斗只打头（强制一发一发点射），热身加入 5 分钟急停-点射循环。",
        {"hs_pct": round(ctx["hs_pct"], 1), "hs_kills": ctx["hs_kills"], "kills": ctx["kills"]},
    )


def rule_kast_low(ctx: dict[str, Any]) -> dict[str, Any] | None:
    if ctx["kast"] is None or ctx["kast"] >= 65.0:
        return None
    return _card(
        "kast_low", "high", "KAST 偏低（回合影响力不足）",
        f"{ctx['map']}：KAST {ctx['kast']:.0f}%——超过三成的回合你没有击杀/助攻/存活/被补枪，"
        "属于“隐身回合”过多。",
        "复盘无贡献回合的死亡时间点：多数是中期无信息对枪。减少无道具支援的干拉。",
        {"kast": round(ctx["kast"], 1)},
    )


def rule_side_dpr_imbalance(ctx: dict[str, Any]) -> dict[str, Any] | None:
    dpr_t, dpr_ct = ctx.get("dpr_t"), ctx.get("dpr_ct")
    if not dpr_t or not dpr_ct or min(dpr_t, dpr_ct) <= 0:
        return None
    high_side, low_side = ("T", "CT") if dpr_t > dpr_ct else ("CT", "T")
    ratio = max(dpr_t, dpr_ct) / min(dpr_t, dpr_ct)
    if ratio < 1.30:
        return None
    return _card(
        "side_dpr_imbalance", "medium", f"{high_side} 阵营死亡明显偏多",
        f"{ctx['map']}：{high_side} 侧 DPR {max(dpr_t, dpr_ct):.2f}，是 "
        f"{low_side} 侧（{min(dpr_t, dpr_ct):.2f}）的 {ratio:.1f} 倍。",
        f"针对 {high_side} 侧复盘前 30 秒走位：是否重复同一节奏被对手阅读；改变默认站位/前压时机。",
        {"dpr_t": round(dpr_t, 3), "dpr_ct": round(dpr_ct, 3)},
    )


def rule_trade_heavy(ctx: dict[str, Any]) -> dict[str, Any] | None:
    deaths = ctx["deaths"]
    if deaths < 8:
        return None
    share = ctx["traded_deaths"] / deaths
    if share <= 0.35:
        return None
    return _card(
        "trade_heavy", "low", "死亡多被补枪（站位可能脱节）",
        f"{ctx['map']}：你 {share:.0%} 的死亡随后被队友补枪（{ctx['traded_deaths']}/{deaths}）。"
        "被补枪是好事，但比例过高说明你常以 1 换 1 的方式白给首枪。",
        "与队友保持可补枪距离的同时，优先打有撤退路线的枪位；拉枪前先用道具逼退直架。",
        {"traded_deaths": ctx["traded_deaths"], "deaths": deaths, "share": round(share, 3)},
    )


def rule_no_multikill(ctx: dict[str, Any]) -> dict[str, Any] | None:
    if ctx["kills"] < 10 or ctx["multikill_rounds"] > 0:
        return None
    return _card(
        "no_multikill", "low", "缺乏多杀回合",
        f"{ctx['map']}：{ctx['kills']} 个击杀分散在单杀回合里，没有任何 2K+ 回合，"
        "说明连杀/收割能力没有兑现。",
        "练习扫射转移（spray transfer）：aim_botz 双目标连扫；实战中拿到首杀后不要急着换弹/换位。",
        {"kills": ctx["kills"], "multikill_rounds": 0},
    )


def rule_passive_survivor(ctx: dict[str, Any]) -> dict[str, Any] | None:
    if ctx["survival"] < 55.0 or ctx["kpr"] >= 0.60:
        return None
    return _card(
        "passive_survivor", "medium", "高存活低产出（偏保守）",
        f"{ctx['map']}：存活率 {ctx['survival']:.0f}% 但 KPR 只有 {ctx['kpr']:.2f}——"
        "活着却没有参与交火，队伍长期 4v5。",
        "中期主动跟队推进一次：补枪位紧跟突破手 2 秒内进场，把存活转化为补枪击杀。",
        {"survival": round(ctx["survival"], 1), "kpr": round(ctx["kpr"], 3)},
    )


def rule_utility_low(ctx: dict[str, Any]) -> dict[str, Any] | None:
    upr = ctx.get("utility_per_round")
    if upr is None or ctx["rounds"] < 8 or upr >= 0.30:
        return None
    return _card(
        "utility_low", "medium", "道具使用不足",
        f"{ctx['map']}：每回合仅 {upr:.2f} 次道具引爆（闪光/烟/雷/火），"
        "相当于大部分回合干拉。",
        "每张图背 2 颗基础烟 + 1 颗反清闪；开局先想“这颗道具帮谁”，再决定买不买。",
        {"utility_per_round": round(upr, 3), "detail": ctx.get("utility_detail", {})},
    )


def rule_clutch_losses(ctx: dict[str, Any]) -> dict[str, Any] | None:
    lost = ctx.get("clutch_losses", 0)
    if lost < 3:
        return None
    return _card(
        "clutch_losses", "low", "残局收尾能力不足",
        f"{ctx['map']}：你 {lost} 次以最后存活身份输掉残局（1vX 被打掉）。",
        "残局优先保枪还是尝试要看经济；尝试时利用假下包/假脚步逼对手先动。",
        {"clutch_losses": lost},
    )


def rule_econ_negative(ctx: dict[str, Any]) -> dict[str, Any] | None:
    delta = ctx.get("econ_delta")
    if delta is None or delta >= -0.05:
        return None
    return _card(
        "econ_negative", "low", "逆风局击杀效率低（近似）",
        f"{ctx['map']}：经济加权击杀差 {delta:+.2f}（近似值）——装备劣势回合你很难换回人头。",
        "ECO 局以堆叠赌点/前压缴枪为目标，不与长枪远点对枪；半起局优先冲锋枪近点。",
        {"econ_delta": round(delta, 3)},
    )


RULES: list[Rule] = [
    rule_fk_rate_low,
    rule_fk_attempt_low,
    rule_pistol_weak,
    rule_hs_low,
    rule_kast_low,
    rule_side_dpr_imbalance,
    rule_trade_heavy,
    rule_no_multikill,
    rule_passive_survivor,
    rule_utility_low,
    rule_clutch_losses,
    rule_econ_negative,
]

_PISTOL_ROUND_NUMS = {1, 13}


def build_context(parsed: Any, metrics_by_side: dict[str, Any], map_name: str, steamid: int) -> dict[str, Any]:
    """Flatten everything the rules need into one dict."""

    all_m = metrics_by_side["ALL"]
    t_m = metrics_by_side.get("T")
    ct_m = metrics_by_side.get("CT")

    def g(name: str, default: float = 0.0) -> float:
        return float(getattr(all_m, name, default) or default)

    coverage = getattr(parsed, "matrix_coverage", {}) or {}
    excluded = set(coverage.get("excluded_rounds") or [])
    formal = [item for item in parsed.counted_rounds if item.is_formal]
    reliable = {
        item.index for item in formal
        if not coverage or item.round_num not in excluded
    }
    steamid = int(steamid)

    pistol_rounds = 0
    pistol_kills = 0
    clutch_losses = 0
    for item in formal:
        kills = 0
        for entry in getattr(parsed, "kill_feed", []) or []:
            if entry.get("round_index") != item.index or entry.get("bot_controlled"):
                continue
            if entry.get("attacker") == steamid:
                kills += 1
            if (
                entry.get("victim") == steamid
                and item.index in reliable
                and entry.get("victim_alive") == 1
                and (entry.get("attacker_alive") or 0) >= 2
            ):
                clutch_losses += 1
        if item.round_num in _PISTOL_ROUND_NUMS:
            pistol_rounds += 1
            pistol_kills += kills

    usage = (getattr(parsed, "utility_usage", {}) or {}).get(steamid, {})
    rounds = int(getattr(all_m, "rounds", 0) or len(formal)) or 1
    return {
        "map": map_name,
        "rounds": rounds,
        "kills": int(g("kills")),
        "deaths": int(g("deaths")),
        "kpr": g("kpr"),
        "dpr_t": float(getattr(t_m, "dpr", 0.0)) if t_m else 0.0,
        "dpr_ct": float(getattr(ct_m, "dpr", 0.0)) if ct_m else 0.0,
        "hs_pct": g("hs_pct"),
        "hs_kills": int(g("headshot_kills")),
        "kast": g("kast") if getattr(all_m, "available", False) else None,
        "survival": g("survival"),
        "opening_kills": int(g("opening_kills")),
        "opening_deaths": int(g("opening_deaths")),
        "traded_deaths": int(g("traded_deaths")),
        "multikill_rounds": int(g("multikill_rounds")),
        "econ_delta": float(getattr(all_m, "econ_delta", 0.0) or 0.0),
        "pistol_rounds": pistol_rounds,
        "pistol_round_kills": pistol_kills,
        "clutch_losses": clutch_losses,
        "utility_per_round": (sum(int(v) for v in usage.values()) / rounds) if usage else 0.0,
        "utility_detail": dict(usage),
    }


def build_coach_report(parsed: Any, metrics_by_side: dict[str, Any], map_name: str, steamid: int) -> dict[str, Any]:
    ctx = build_context(parsed, metrics_by_side, map_name, steamid)
    cards = [card for rule in RULES if (card := rule(ctx)) is not None]
    cards.sort(key=lambda card: (SEVERITY_ORDER.get(card["severity"], 3), card["id"]))
    return {"cards": cards, "context": ctx, "rule_count": len(RULES)}
