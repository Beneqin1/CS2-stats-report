"""Build the serialisable payload consumed by the HTML report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import (
    APP_NAME,
    APP_VERSION,
    APPROX_NOTE,
    COMPARE_METRICS,
    RADAR_AXES,
    RADAR_MAX,
    RADAR_PRO_AVG,
    SIDE_CT,
    SIDE_T,
    SIDES,
    TARGET_DEFAULT,
)
from .demo_parser import ParsedDemo, Player
from .coach import build_coach_report
from .highlights import detect_highlights, goto_commands
from .style_dna import build_style_dna
from .rating import (
    Metrics,
    PlayerMetrics,
    average_metrics,
    average_rating,
    build_player_metrics,
    weighted_team_average,
)
from .utils import clamp

ALL = "ALL"
SIDE_KEYS = (ALL, SIDE_T, SIDE_CT)
SCOPE_LABELS = (
    ("all", "All"),
    ("first", "First kills"),
    ("clutch", "残局 1vX"),
    ("awp", "AWP"),
    ("pistol", "手枪"),
)


def _metrics_payload(metrics: Metrics) -> dict[str, Any]:
    return {
        "rounds": metrics.rounds,
        "kills": metrics.kills,
        "deaths": metrics.deaths,
        "assists": metrics.assists,
        "flash_assists": metrics.flash_assists,
        "headshot_kills": metrics.headshot_kills,
        "opening_kills": metrics.opening_kills,
        "opening_deaths": metrics.opening_deaths,
        "traded_deaths": metrics.traded_deaths,
        "multikill_2k": metrics.multikill_2k,
        "multikill_3k": metrics.multikill_3k,
        "multikill_4k": metrics.multikill_4k,
        "multikill_5k": metrics.multikill_5k,
        "multikill_rounds": metrics.multikill_rounds,
        "mvps": metrics.mvps,
        "damage": metrics.damage,
        "utility_damage": metrics.utility_damage,
        "kast_rounds": metrics.kast_rounds,
        "survived_rounds": metrics.survived_rounds,
        "kpr": metrics.kpr,
        "dpr": metrics.dpr,
        "apr": metrics.apr,
        "kast": metrics.kast,
        "adr": metrics.adr,
        "survival": metrics.survival,
        "hs_pct": metrics.hs_pct,
        "fk_rate": metrics.fk_rate,
        "impact": metrics.impact,
        "base_rating": metrics.base_rating,
        "econ_delta": metrics.econ_delta,
        "swing_pct": metrics.swing_pct,
        "rating": metrics.rating,
        "mk_rating": metrics.mk_rating,
        "plus_minus": metrics.plus_minus,
        "kd": metrics.kd,
        "available": metrics.available,
    }


_BASE_TABLE_FIELDS = (
    "kills", "deaths", "assists", "adr", "kast", "opening_kills",
    "headshot_kills", "flash_assists", "traded_deaths", "multikill_rounds",
    "mvps", "rating", "kdr", "plus_minus", "kd", "swing",
    "kpr", "dpr", "apr", "opening_deaths", "fk_rate", "hs_pct",
)


def _field_support(demo: ParsedDemo, player: Player) -> dict[str, list[str]]:
    """Return template-friendly per-side availability lists.

    Demo parsers can expose either a flat ``{field: bool}`` map (scoreboard
    layer) or a ``{side: [fields]}`` map (older exact parser).  Normalise both.
    """

    raw = getattr(demo, "field_availability", {}).get(player.steamid, {})
    result: dict[str, list[str]] = {}
    for side in SIDE_KEYS:
        if not raw:
            result[side] = list(_BASE_TABLE_FIELDS)
            continue
        if isinstance(raw.get(side), (list, tuple, set)):
            result[side] = [str(item) for item in raw[side]]
            continue
        fields: list[str] = []
        for key in _BASE_TABLE_FIELDS:
            if key == "opening_kills":
                enabled = bool(raw.get("opening_kills", True))
            else:
                enabled = bool(raw.get(key, True))
            if enabled and not bool(raw.get(f"{key}_{side}", True)):
                enabled = False
            if enabled:
                fields.append(key)
        result[side] = fields
    return result


def _player_payload(demo: ParsedDemo, pm: PlayerMetrics) -> dict[str, Any]:
    player = pm.player
    team = demo.teams.get(player.team_key)
    return {
        "steamid": player.steamid,
        "name": player.name,
        "team": player.team_key,
        "team_name": team.name if team else player.team_key,
        "color": team.color if team else "#98ffff",
        "exact": {
            "kills": player.exact_int("kills"),
            "deaths": player.exact_int("deaths"),
            "assists": player.exact_int("assists"),
            "adr": player.exact_float("adr"),
            "kast": player.exact_float("kast"),
            "opening_kills": player.exact_int("opening_kills"),
        },
        "metrics": {side: _metrics_payload(pm.for_side(side)) for side in SIDE_KEYS},
        "field_support": _field_support(demo, player),
    }


def _team_payload(demo: ParsedDemo, team_key: str, pms: list[PlayerMetrics]) -> dict[str, Any]:
    team = demo.teams[team_key]
    members = [pm for pm in pms if pm.player.team_key == team_key]
    return {
        "key": team.key,
        "name": team.name,
        "color": team.color,
        "start_side": team.start_side,
        "score": team.score,
        "total_kills": team.total_kills,
        "avg_rating": weighted_team_average(pm.all for pm in members),
        "players": [pm.player.steamid for pm in members],
    }


def _sort_key(pm: PlayerMetrics, side: str) -> tuple[float, int, str]:
    metrics = pm.for_side(side)
    return (
        -metrics.rating if metrics.available else 1_000.0,
        -metrics.kills,
        pm.player.name.casefold(),
    )


def _find_target(pms: list[PlayerMetrics], requested: str) -> tuple[PlayerMetrics | None, bool]:
    needle = (requested or "").strip().casefold()
    if needle:
        for pm in pms:
            if pm.player.name.strip().casefold() == needle:
                return pm, True
    if not pms:
        return None, False
    best = max(pms, key=lambda pm: (pm.all.rating, pm.all.kills, pm.player.name.casefold()))
    return best, False


def _average_payload(pms: list[PlayerMetrics]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for side in SIDE_KEYS:
        result[side] = average_metrics(pm.for_side(side) for pm in pms)
    return result


def _radar_payload(
    demo: ParsedDemo,
    target: PlayerMetrics,
    averages: dict[str, dict[str, float]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    support = getattr(demo, "field_availability", {}).get(
        target.player.steamid, {}
    )
    flat_support = support if support and not isinstance(next(iter(support.values()), None), (list, tuple, set)) else {}
    for side in SIDE_KEYS:
        metrics = target.for_side(side)
        avg = averages[side]
        fields = set(support.get(side) or []) if isinstance(support.get(side), (list, tuple, set)) else {
            key for key, enabled in flat_support.items() if enabled
        }
        axes = []
        for key, label, decimals in RADAR_AXES:
            value = float(getattr(metrics, key))
            avg_value = float(avg.get(key, 0.0))
            # Fixed scale: outer ring = 2x pro-scene average (RADAR_PRO_AVG).
            # Values beyond it are meant to burst out of the grid, so the
            # axis max must NOT grow with the data here.
            max_value = float(RADAR_MAX.get(key, 1.0))
            axes.append(
                {
                    "key": key,
                    "label": label,
                    "value": value,
                    "avg": avg_value,
                    "max": max_value,
                    "baseline": float(RADAR_PRO_AVG.get(key, max_value / 2.0)),
                    "burst": bool(metrics.available) and max_value > 0 and value > max_value,
                    "decimals": decimals,
                    "available": bool(metrics.available),
                }
            )
        result[side] = {
            "available": metrics.available,
            "axes": axes,
        }
    return result


def _format_value(key: str, value: float, decimals: int) -> str:
    if key in ("kast", "swing_pct"):
        return f"{value:.{decimals}f}%"
    return f"{value:.{decimals}f}"


def _compare_payload(
    demo: ParsedDemo,
    target: PlayerMetrics,
    averages: dict[str, dict[str, float]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    support = getattr(demo, "field_availability", {}).get(
        target.player.steamid, {}
    )
    flat_support = support if support and not isinstance(next(iter(support.values()), None), (list, tuple, set)) else {}
    for side in SIDE_KEYS:
        metrics = target.for_side(side)
        avg = averages[side]
        fields = set(support.get(side) or []) if isinstance(support.get(side), (list, tuple, set)) else {
            key for key, enabled in flat_support.items() if enabled
        }
        rows = []
        for key, label, decimals, color in COMPARE_METRICS:
            value = float(getattr(metrics, key))
            avg_value = float(avg.get(key, 0.0))
            higher_is_better = key != "dpr"
            if key == "swing_pct":
                scale = max(abs(value), abs(avg_value), 5.0)
                bar = clamp(70.0 + 30.0 * (value - avg_value) / scale, 5.0, 100.0)
                better = value > avg_value + 1e-9
                worse = value < avg_value - 1e-9
            else:
                if abs(avg_value) < 1e-12:
                    ratio = 1.35 if value > 0 else 1.0
                else:
                    ratio = (value / avg_value) if higher_is_better else (avg_value / value if value > 0 else 1.0)
                bar = clamp(70.0 * ratio, 5.0, 100.0)
                better = (value > avg_value + 1e-9) if higher_is_better else (value < avg_value - 1e-9)
                worse = (value < avg_value - 1e-9) if higher_is_better else (value > avg_value + 1e-9)
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "value": value,
                    "average": avg_value,
                    "bar_pct": bar,
                    "avg_pct": 70.0,
                    "better": bool(better),
                    "worse": bool(worse),
                    "value_text": _format_value(key, value, decimals),
                    "avg_text": _format_value(key, avg_value, decimals),
                    "color": color,
                    "available": (
                        metrics.available
                        and (
                            not fields
                            or key not in ("kast", "adr", "swing_pct")
                            or (key if key != "swing_pct" else "swing") in fields
                        )
                    ),
                }
            )
        result[side] = {"rows": rows, "available": metrics.available}
    return result


def _matrix_payload(
    demo: ParsedDemo,
    pms: list[PlayerMetrics],
) -> dict[str, Any]:
    teams = demo.team_list
    empty_world = {
        scope: {side: {"row": [], "col": []} for side in SIDE_KEYS}
        for scope, _ in SCOPE_LABELS
    }
    if len(teams) < 2:
        return {
            "scopes": [],
            "sides": list(SIDE_KEYS),
            "rows": [],
            "cols": [],
            "cells": {},
            "world": empty_world,
            "coverage": {},
        }
    team_a, team_b = teams[0], teams[1]
    rows = [pm.player for pm in pms if pm.player.team_key == team_a.key]
    cols = [pm.player for pm in pms if pm.player.team_key == team_b.key]
    matrix_world = getattr(demo, "matrix_world", {}) or {}

    def cell_counts(scope: str, side: str, attacker: Player, victim: Player) -> tuple[int, int]:
        if side == ALL:
            forward = sum(
                int(demo.matrix.get(scope, {}).get(each_side, {}).get((attacker.steamid, victim.steamid), 0))
                for each_side in SIDES
            )
            reverse = sum(
                int(demo.matrix.get(scope, {}).get(each_side, {}).get((victim.steamid, attacker.steamid), 0))
                for each_side in SIDES
            )
            return forward, reverse
        forward = int(demo.matrix.get(scope, {}).get(side, {}).get((attacker.steamid, victim.steamid), 0))
        reverse = int(demo.matrix.get(scope, {}).get(side, {}).get((victim.steamid, attacker.steamid), 0))
        return forward, reverse

    def world_count(scope: str, side: str, victim: Player) -> int:
        if side == ALL:
            return sum(
                int(matrix_world.get(scope, {}).get(each_side, {}).get(victim.steamid, 0))
                for each_side in SIDES
            )
        return int(matrix_world.get(scope, {}).get(side, {}).get(victim.steamid, 0))

    def scope_has_data(scope: str) -> bool:
        for side in SIDES:
            if demo.matrix.get(scope, {}).get(side):
                return True
            if matrix_world.get(scope, {}).get(side):
                return True
        return False

    scopes = [
        (key, label)
        for key, label in SCOPE_LABELS
        if key == "all" or scope_has_data(key)
    ]
    cells: dict[str, dict[str, list[list[dict[str, int]]]]] = {}
    world: dict[str, dict[str, dict[str, list[int]]]] = {}
    for scope, _ in scopes:
        cells[scope] = {}
        world[scope] = {}
        for side in SIDE_KEYS:
            grid = []
            for row_player in rows:
                row = []
                for col_player in cols:
                    forward, reverse = cell_counts(scope, side, row_player, col_player)
                    row.append({"f": forward, "r": reverse})
                grid.append(row)
            cells[scope][side] = grid
            world[scope][side] = {
                # Extra bottom row: world/suicide deaths of each column player.
                "row": [world_count(scope, side, col_player) for col_player in cols],
                # Extra right column: world/suicide deaths of each row player.
                "col": [world_count(scope, side, row_player) for row_player in rows],
            }

    available = False
    if demo.matrix:
        for side in SIDE_KEYS:
            grid = cells["all"][side]
            if any(cell["f"] or cell["r"] for line in grid for cell in line):
                available = True
                break
            if any(world["all"][side]["row"]) or any(world["all"][side]["col"]):
                available = True
                break
    coverage = getattr(demo, "matrix_coverage", {}) or {}
    covered = int(coverage.get("covered", 0) or 0)
    total = int(coverage.get("total", 0) or 0)
    if not available:
        note = coverage.get("reason") or "事件层与计分板属性不一致，Kill Matrix 无法可靠重建，已隐藏。"
    elif covered < total:
        note = (
            f"事件层逐回合与计分板差分核对：覆盖 {covered}/{total} 回合"
            f"（排除回合：{', '.join(str(n) for n in coverage.get('excluded_rounds') or [])}）；"
            "环境/自杀死亡在“环境”行/列。"
        )
    else:
        note = "事件层逐回合与计分板差分核对一致；环境/自杀死亡在“环境”行/列。"
    return {
        "available": available,
        "note": note,
        "coverage": coverage,
        "scopes": [{"key": key, "label": label} for key, label in scopes],
        "sides": list(SIDE_KEYS),
        "rows": [
            {
                "steamid": player.steamid,
                "name": player.name,
                "team": team_a.key,
                "team_name": team_a.name,
                "color": team_a.color,
            }
            for player in rows
        ],
        "cols": [
            {
                "steamid": player.steamid,
                "name": player.name,
                "team": team_b.key,
                "team_name": team_b.name,
                "color": team_b.color,
            }
            for player in cols
        ],
        "cells": cells,
        "world": world,
    }


def _validation_payload(validation: dict[str, Any] | None) -> dict[str, Any]:
    if validation:
        return validation
    return {
        "status": "unverified",
        "title": "精确字段未校验",
        "message": (
            "尚未用计分板 fixture 校验 K/D/A、ADR、KAST。"
            "精确字段按正式回合事件重建，但本报告未证明它与游戏内计分板逐项相等。"
        ),
        "fixture": None,
        "checks": [],
    }


def build_report(
    demo: ParsedDemo,
    target_name: str = TARGET_DEFAULT,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the full JSON-serialisable report payload."""

    from dataclasses import replace
    from .swing import analyse_match

    pms = build_player_metrics(demo)
    swing_values, swing_meta = analyse_match(demo)
    for pm in pms:
        values = swing_values.get(pm.player.steamid, {})
        pm.all = replace(pm.all, swing_pct=values.get('ALL', 0.0))
        pm.sides = {side: replace(metric, swing_pct=values.get(side, 0.0))
                    for side, metric in pm.sides.items()}
    averages = _average_payload(pms)
    target, target_found = _find_target(pms, target_name)

    table_order: dict[str, list[int]] = {}
    chart_order: dict[str, list[int]] = {}
    for side in SIDE_KEYS:
        ordered: list[int] = []
        for team in demo.team_list:
            members = [pm for pm in pms if pm.player.team_key == team.key]
            members.sort(key=lambda pm: _sort_key(pm, side))
            ordered.extend(pm.player.steamid for pm in members)
        table_order[side] = ordered
        chart = sorted(pms, key=lambda pm: _sort_key(pm, side))
        chart_order[side] = [pm.player.steamid for pm in chart]

    teams = demo.team_list
    best_player = max(pms, key=lambda pm: (pm.all.rating, pm.all.kills), default=None)
    top_score = max((team.score for team in teams), default=0)
    winners = [team for team in teams if team.score == top_score]
    mvp_team = winners[0] if len(winners) == 1 else None
    mvp_pool = [
        pm for pm in pms if mvp_team is not None and pm.player.team_key == mvp_team.key
    ] or pms
    mvp = max(mvp_pool, key=lambda pm: (pm.all.rating, pm.all.kills), default=None)
    summary = {
        "rounds": len(demo.counted_rounds),
        "total_kills": sum(team.total_kills for team in teams),
        "average_rating": average_rating(pm.all for pm in pms),
        "teams": [_team_payload(demo, team.key, pms) for team in teams],
        "top_player": (
            {
                "name": best_player.player.name,
                "rating": best_player.all.rating,
                "team": best_player.player.team_key,
            }
            if best_player
            else None
        ),
        "mvp": (
            {
                "name": mvp.player.name,
                "rating": mvp.all.rating,
                "team": mvp.player.team_key,
                "team_name": demo.teams[mvp.player.team_key].name
                if mvp.player.team_key in demo.teams
                else mvp.player.team_key,
                "winner_team": mvp_team is not None,
            }
            if mvp
            else None
        ),
    }

    scoreboard = getattr(demo, "scoreboard_meta", {}) or {}
    warnings = list(getattr(demo, "side_warnings", []) or []) + list(
        getattr(demo, "consistency_warnings", []) or []
    )
    if swing_meta.get('warning'):
        warnings.append(swing_meta['warning'])
    warnings = [w.replace("Swing 使用计分板每回合 K/A/D 近似；", "Swing 新口径见 WPA 警告；") for w in warnings]
    fixture_meta = validation or {}
    precision = fixture_meta.get("fixture_precision") or {}
    try:
        adr_decimals = max(0, min(4, int(precision.get("adr", 1))))
    except (TypeError, ValueError):
        adr_decimals = 1
    adr_rounding = str(fixture_meta.get("adr_rounding") or "half_up").strip().lower()
    matrix_payload = _matrix_payload(demo, pms)
    matrix_available = bool(matrix_payload.get("available"))

    highlights_payload = detect_highlights(demo)
    demo_path = str(getattr(demo, "path", "") or "")
    for card in highlights_payload.get("cards", []):
        card["commands"] = goto_commands(demo_path, card["goto_tick"])

    style_dna_payload = None
    coach_payload = None
    if target is not None:
        style_dna_payload = build_style_dna(demo, target.player.steamid)
        style_dna_payload["name"] = target.player.name
        coach_payload = build_coach_report(
            demo,
            {side: target.for_side(side) for side in SIDE_KEYS},
            demo.map_name[:1].upper() + demo.map_name[1:] if demo.map_name else demo.map_name,
            target.player.steamid,
        )

    target_payload = None
    if target:
        target_payload = {
            "requested": target_name,
            "found": target_found,
            "name": target.player.name,
            "team": target.player.team_key,
            "team_name": demo.teams[target.player.team_key].name,
            "color": demo.teams[target.player.team_key].color,
            "metrics": {side: _metrics_payload(target.for_side(side)) for side in SIDE_KEYS},
        }

    return {
        "meta": {
            "app": APP_NAME,
            "version": APP_VERSION,
            "map": demo.map_name[:1].upper() + demo.map_name[1:] if demo.map_name else demo.map_name,
            "demo_file": demo.path.name,
            "demo_sha256": demo.sha256,
            "tickrate": demo.tickrate,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rounds": len(demo.counted_rounds),
            "total_kills": summary["total_kills"],
            "approx_note": APPROX_NOTE,
            "target_requested": target_name,
            "target_found": target_found,
            "target_name": target.player.name if target else None,
            "player_count": len(pms),
            "adr_decimals": adr_decimals,
            "adr_rounding": adr_rounding,
            "scoreboard": scoreboard,
            "matrix_available": matrix_available,
            "warnings": warnings,
            "swing": swing_meta,
            "data_quality": getattr(demo, "data_quality", {}) or {},
            "from_cache": bool(getattr(demo, "from_cache", False)),
        },
        "summary": summary,
        "validation": _validation_payload(validation),
        "target": target_payload,
        "players": [_player_payload(demo, pm) for pm in pms],
        "averages": averages,
        "table_order": table_order,
        "chart_order": chart_order,
        "radar": _radar_payload(demo, target, averages) if target else None,
        "compare": _compare_payload(demo, target, averages) if target else None,
        "matrix": matrix_payload,
        "style_dna": style_dna_payload,
        "coach": coach_payload,
        "highlights": highlights_payload,
    }
