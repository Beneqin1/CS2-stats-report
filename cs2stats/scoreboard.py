"""Authoritative scoreboard-property layer for CS2 demos.

The event stream in some locally hosted/bot-heavy demos misattributes
``player_death`` rows (the demo recorder's userid can own several bots).  The
player controller properties (``kills_total``, ``damage_total`` ...) are the
same values the in-game scoreboard renders, so they are the source of truth for
K/D/A and ADR.

This module reads those properties at the end of each formal round with
demoparser2 and rewrites the exact counters.  ``headshot_kills_total`` is also
read from the scoreboard.  Open Kills are inferred from the first increase of
``kills_total`` inside each formal round.  Event-derived fields that cannot be
recovered from those properties (Kill Matrix, flash assists, traded deaths,
Swing) are marked unavailable when the event layer disagrees with the
scoreboard.
"""

from __future__ import annotations

from typing import Any

from .config import SIDE_CT, SIDE_T
from .demo_parser import Counters, ParsedDemo, Player

_SCORE_PROPS = [
    "kills_total",
    "deaths_total",
    "assists_total",
    "headshot_kills_total",
    "damage_total",
    "total_rounds_played",
    "team_num",
    "name",
    "health",
    "kills_this_round",
    "deaths_this_round",
    "assists_this_round",
    "damage_this_round",
]


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        try:
            return int(text)
        except ValueError:
            return int(float(text))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _rows_by_steamid(frame, tick: int | None = None) -> dict[int, dict[str, Any]]:
    if frame is None:
        return {}
    rows: dict[int, dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        if tick is not None and _int(record.get("tick")) != tick:
            continue
        sid = _int(record.get("steamid"))
        if sid:
            rows[sid] = record
    return rows


def _side_of(record: dict[str, Any]) -> str | None:
    team_num = _int(record.get("team_num"), -1)
    if team_num == 2:
        return SIDE_T
    if team_num == 3:
        return SIDE_CT
    return None


def _round_end_ticks(parser) -> dict[int, int]:
    try:
        frame = parser.parse_event("round_end")
    except Exception:
        return {}
    ticks: dict[int, int] = {}
    for record in frame.to_dict("records"):
        round_num = _int(record.get("round"))
        tick = _int(record.get("tick"))
        if round_num > 0 and tick > 0:
            ticks[round_num] = tick
    return ticks


def _round_end_tick(round_item) -> int:
    return _int(getattr(round_item, "end_tick", 0) or getattr(round_item, "official_end_tick", 0))


def _opening_counts(parser, parsed: ParsedDemo, formal_rounds) -> tuple[dict[int, int], dict[int, dict[str, int]], list[str]]:
    """Infer round-opening kills from scoreboard kill deltas.

    The event stream can misattribute both attacker and victim in bot-heavy
    demos, but the scoreboard kill counter is authoritative.  For each formal
    round we find the first tick where any player's ``kills_total`` increases.
    A tie (two or more players at the same tick) is counted as ambiguous and
    skipped rather than guessed.
    """
    warnings: list[str] = []
    counts: dict[int, int] = {}
    sides: dict[int, dict[str, int]] = {}
    try:
        frame = parser.parse_ticks(["kills_total"])
    except Exception as exc:
        warnings.append(f"Open Kills 计分板差分不可用（{exc}）。")
        return counts, sides, warnings
    if frame is None or frame.empty:
        warnings.append("Open Kills 计分板差分为空。")
        return counts, sides, warnings
    frame = frame.sort_values(["steamid", "tick"])
    for round_item in formal_rounds:
        start = _int(getattr(round_item, "live_start", 0))
        end = _round_end_tick(round_item)
        if not start or not end or end <= start:
            continue
        window = frame[(frame["tick"] >= start) & (frame["tick"] <= end)]
        if window.empty:
            continue
        candidates: list[tuple[int, int]] = []
        for sid, group in window.groupby("steamid"):
            group = group.sort_values("tick")
            base = _int(group["kills_total"].iloc[0])
            increased = group[group["kills_total"] > base]
            if not increased.empty:
                candidates.append((_int(increased["tick"].iloc[0]), _int(sid)))
        if not candidates:
            continue
        first_tick = min(tick for tick, _ in candidates)
        winners = [sid for tick, sid in candidates if tick == first_tick]
        if len(winners) != 1:
            warnings.append(
                f"第 {round_item.round_num} 回合有 {len(winners)} 名选手在同一 tick 取得首杀，"
                "Open Kills 无法唯一归属，已跳过。"
            )
            continue
        sid = winners[0]
        counts[sid] = counts.get(sid, 0) + 1
        player = parsed.player_by_steamid(sid) if hasattr(parsed, "player_by_steamid") else None
        if player is None:
            player = next((item for item in parsed.players if int(item.steamid) == sid), None)
        if player is not None:
            side = parsed.team_side_for(round_item.index, player.team_key)
            if side in (SIDE_T, SIDE_CT):
                sides.setdefault(sid, {SIDE_T: 0, SIDE_CT: 0})[side] += 1
    return counts, sides, warnings


def apply_scoreboard(parsed: ParsedDemo, demo_path: str) -> bool:
    """Rewrite exact counters from scoreboard properties.

    Returns True when the layer was applied.  On any parser/import problem the
    existing event-derived model is left untouched and a warning is appended.
    """

    try:
        import demoparser2
        import pandas  # noqa: F401 - demoparser2 returns pandas frames
    except Exception as exc:  # pragma: no cover - packaging/install problem
        parsed.side_warnings.append(f"计分板属性不可用（{exc}）；精确层退回事件重建。")
        parsed.scoreboard_meta["applied"] = False
        return False

    try:
        parser = demoparser2.DemoParser(str(demo_path))
        end_ticks = _round_end_ticks(parser)
        formal_rounds = [item for item in parsed.counted_rounds if item.round_num in end_ticks]
        if not formal_rounds:
            parsed.side_warnings.append("计分板属性未找到正式回合的 round_end 事件。")
            parsed.scoreboard_meta["applied"] = False
            return False

        ticks = [end_ticks[item.round_num] for item in formal_rounds]
        final_tick = max(ticks)
        final_frame = parser.parse_ticks(_SCORE_PROPS, ticks=[final_tick])
        round_frame = parser.parse_ticks(_SCORE_PROPS, ticks=ticks)
    except Exception as exc:
        parsed.side_warnings.append(f"计分板属性读取失败（{exc}）；精确层退回事件重建。")
        parsed.scoreboard_meta["applied"] = False
        return False

    final_rows = _rows_by_steamid(final_frame, final_tick)
    if not final_rows:
        parsed.side_warnings.append("计分板属性为空；精确层退回事件重建。")
        parsed.scoreboard_meta["applied"] = False
        return False

    by_round: dict[tuple[int, int], dict[str, Any]] = {}
    for record in round_frame.to_dict("records"):
        sid = _int(record.get("steamid"))
        tick = _int(record.get("tick"))
        if not sid or tick not in ticks:
            continue
        round_item = formal_rounds[ticks.index(tick)]
        by_round[(sid, round_item.round_num)] = record

    total_rounds = _int(
        next(iter(final_rows.values())).get("total_rounds_played"),
        len(formal_rounds),
    ) or len(formal_rounds)

    opening_counts, opening_sides, opening_warnings = _opening_counts(parser, parsed, formal_rounds)
    parsed.side_warnings.extend(opening_warnings)

    event_reliable = True
    differences: list[dict[str, Any]] = []

    for player in parsed.players:
        final = final_rows.get(player.steamid)
        if final is None:
            # A name fallback is only used when the steamid is absent from the
            # property frame (some demos expose bots under transient ids).
            for sid, candidate in final_rows.items():
                if str(candidate.get("name") or "").strip().casefold() == player.name.strip().casefold():
                    final = candidate
                    break
        if final is None:
            parsed.side_warnings.append(f"计分板属性缺少选手 {player.name}（{player.steamid}）。")
            continue

        old_raw = player.raw
        old_traded = set(player.traded_rounds)
        old_exact = dict(player.exact)

        new_raw = Counters()
        new_raw.rounds = total_rounds
        new_raw.kills = _int(final.get("kills_total"))
        new_raw.deaths = _int(final.get("deaths_total"))
        new_raw.assists = _int(final.get("assists_total"))
        new_raw.headshot_kills = _int(final.get("headshot_kills_total"))
        new_raw.opening_kills = opening_counts.get(player.steamid, 0)
        new_raw.damage = _float(final.get("damage_total"))
        # Keep the event-derived economy/swing weights for the approximate
        # Rating layer.  Exact K/D/A/ADR still come from the scoreboard; these
        # weights only feed Rating/Swing, which are explicitly approximate.
        new_raw.kill_weight = old_raw.kill_weight or float(new_raw.kills)
        new_raw.death_weight = old_raw.death_weight or float(new_raw.deaths)
        # Filled below: event swing for reliable players, scoreboard-round
        # fallback for players whose event attribution disagrees.
        new_raw.swing_sum = old_raw.swing_sum

        side = {SIDE_T: Counters(), SIDE_CT: Counters()}
        for side_key in (SIDE_T, SIDE_CT):
            side[side_key].opening_kills = opening_sides.get(player.steamid, {}).get(side_key, 0)
        kast_rounds = 0
        survived_rounds = 0
        multikill = {2: 0, 3: 0, 4: 0, 5: 0}
        multikill_rounds = 0
        side_kast = {SIDE_T: 0, SIDE_CT: 0}
        side_survived = {SIDE_T: 0, SIDE_CT: 0}
        side_multikill = {SIDE_T: {2: 0, 3: 0, 4: 0, 5: 0}, SIDE_CT: {2: 0, 3: 0, 4: 0, 5: 0}}
        side_swing = {SIDE_T: 0.0, SIDE_CT: 0.0}
        scoreboard_swing = 0.0

        previous = {"kills": 0, "deaths": 0, "assists": 0, "headshots": 0, "damage": 0.0}
        for round_item in formal_rounds:
            row = by_round.get((player.steamid, round_item.round_num))
            if row is None:
                continue
            round_side = _side_of(row)
            parsed_side = parsed.team_side_for(round_item.index, player.team_key)
            if round_side is None:
                round_side = parsed_side
            if round_side not in (SIDE_T, SIDE_CT):
                round_side = parsed_side or SIDE_T
            if parsed_side and round_side != parsed_side:
                parsed.side_warnings.append(
                    f"{player.name}：第 {round_item.round_num} 回合计分板阵营 "
                    f"{round_side} 与回合映射 {parsed_side} 不一致。"
                )

            # The *_this_round properties are not reliable at every round_end
            # tick in bot-heavy demos (some rounds report 0 while the total
            # jumps by several).  Differences of the authoritative totals are.
            total_kills = _int(row.get("kills_total"))
            total_deaths = _int(row.get("deaths_total"))
            total_assists = _int(row.get("assists_total"))
            total_damage = _float(row.get("damage_total"))
            total_headshots = _int(row.get("headshot_kills_total"))
            kills = max(0, total_kills - previous["kills"])
            deaths = max(0, total_deaths - previous["deaths"])
            assists = max(0, total_assists - previous["assists"])
            headshots = max(0, total_headshots - previous["headshots"])
            damage = max(0.0, total_damage - previous["damage"])
            previous = {
                "kills": total_kills,
                "deaths": total_deaths,
                "assists": total_assists,
                "headshots": total_headshots,
                "damage": total_damage,
            }

            counter = side[round_side]
            counter.rounds += 1
            counter.kills += kills
            counter.deaths += deaths
            counter.assists += assists
            counter.headshot_kills += headshots
            counter.damage += damage
            counter.kill_weight += float(kills)
            counter.death_weight += float(deaths)

            # Round-level Swing fallback: the event stream cannot be trusted
            # for bot-takeover demos, but the scoreboard gives exact per-round
            # kills/deaths/assists and the winner.  This keeps the column
            # useful and is explicitly labelled approximate.
            round_swing = 0.0
            if kills or assists or deaths:
                impact = kills + 0.45 * assists
                won_round = parsed.winner_team_for(round_item.index) == player.team_key
                if won_round:
                    round_swing = impact
                else:
                    round_swing = -(0.35 * impact + 0.20 * deaths)
                scoreboard_swing += round_swing
                counter.swing_sum += round_swing
                side_swing[round_side] += round_swing

            killed = kills > 0
            assisted = assists > 0
            survived = deaths == 0
            traded = event_reliable and round_item.index in old_traded
            if killed or assisted or survived or traded:
                kast_rounds += 1
                counter.kast_rounds += 1
                side_kast[round_side] += 1
            if survived:
                survived_rounds += 1
                counter.survived_rounds += 1
                side_survived[round_side] += 1
            if kills >= 2:
                bucket = min(5, kills)
                multikill[bucket] += 1
                multikill_rounds += 1
                counter.multikill_rounds += 1
                side_multikill[round_side][bucket] += 1
                if bucket == 2:
                    counter.multikill_2k += 1
                elif bucket == 3:
                    counter.multikill_3k += 1
                elif bucket == 4:
                    counter.multikill_4k += 1
                else:
                    counter.multikill_5k += 1

        new_raw.kast_rounds = kast_rounds
        new_raw.survived_rounds = survived_rounds
        new_raw.multikill_2k = multikill[2]
        new_raw.multikill_3k = multikill[3]
        new_raw.multikill_4k = multikill[4]
        new_raw.multikill_5k = multikill[5]
        new_raw.multikill_rounds = multikill_rounds

        for each_side in (SIDE_T, SIDE_CT):
            counter = side[each_side]
            counter.kast_rounds = side_kast[each_side]
            counter.survived_rounds = side_survived[each_side]
            counter.multikill_rounds = sum(side_multikill[each_side].values())
            counter.multikill_2k = side_multikill[each_side][2]
            counter.multikill_3k = side_multikill[each_side][3]
            counter.multikill_4k = side_multikill[each_side][4]
            counter.multikill_5k = side_multikill[each_side][5]

        kda_mismatch = (
            old_raw.kills != new_raw.kills
            or old_raw.deaths != new_raw.deaths
            or old_raw.assists != new_raw.assists
        )
        if kda_mismatch:
            new_raw.swing_sum = scoreboard_swing
            for each_side in (SIDE_T, SIDE_CT):
                side[each_side].swing_sum = side_swing[each_side]
        else:
            new_raw.swing_sum = old_raw.swing_sum
            for each_side in (SIDE_T, SIDE_CT):
                side[each_side].swing_sum = player.side[each_side].swing_sum

        player.raw = new_raw
        player.side = side
        player.rounds_played = total_rounds
        if str(final.get("name") or "").strip():
            player.name = str(final.get("name")).strip()

        adr = new_raw.damage / total_rounds if total_rounds else 0.0
        kast = (kast_rounds * 100.0 / total_rounds) if total_rounds else 0.0
        player.exact = {
            "rounds_played": total_rounds,
            "kills": new_raw.kills,
            "deaths": new_raw.deaths,
            "assists": new_raw.assists,
            "flash_assists": old_exact.get("flash_assists", 0),
            "headshot_kills": new_raw.headshot_kills,
            "headshot_pct": (new_raw.headshot_kills * 100.0 / new_raw.kills) if new_raw.kills else 0.0,
            "opening_kills": new_raw.opening_kills,
            "opening_deaths": old_exact.get("opening_deaths", 0),
            "traded_deaths": old_exact.get("traded_deaths", 0),
            "multikill_2k": multikill[2],
            "multikill_3k": multikill[3],
            "multikill_4k": multikill[4],
            "multikill_5k": multikill[5],
            "utility_damage": old_exact.get("utility_damage", 0),
            "adr": adr,
            "kast": kast,
        }

        if kda_mismatch:
            event_reliable = False
            for field_name in ("kills", "deaths", "assists"):
                before = _int(getattr(old_raw, field_name))
                after = _int(getattr(new_raw, field_name))
                if before != after:
                    differences.append(
                        {
                            "player": player.name,
                            "field": field_name,
                            "source": before,
                            "rebuilt": after,
                            "delta": after - before,
                        }
                    )

        availability = {
            "kills": True,
            "deaths": True,
            "assists": True,
            "adr": True,
            "kast": True,
            "opening_kills": True,
            "headshot_kills": True,
            "flash_assists": False,
            "traded_deaths": False,
            "multikill_rounds": True,
            "swing": True,
        }
        for each_side in (SIDE_T, SIDE_CT):
            availability[f"kills_{each_side}"] = True
            availability[f"deaths_{each_side}"] = True
            availability[f"adr_{each_side}"] = True
        parsed.field_availability[player.steamid] = availability

    if not event_reliable:
        parsed.matrix = {}
        for player in parsed.players:
            parsed.field_availability.setdefault(player.steamid, {})["swing"] = True
        parsed.side_warnings.append(
            "事件层（player_death）与计分板属性不一致，疑似 bot 接管/录制者归因问题："
            "Kill Matrix、A(f)、D(t) 已隐藏；K/D/A/ADR/K(hs)、Open Kills 使用计分板属性/差分；"
            "KAST 按计分板每回合 K/A/S 计算，不包含 traded 回合；Swing 使用计分板每回合 K/A/D 近似。"
        )

    for team in parsed.teams.values():
        team.total_kills = sum(player.exact_int("kills") for player in team.players)

    parsed.stats_differences = differences
    # Death diagnostics were computed from the untrusted event stream; the
    # scoreboard totals above are authoritative, so the old per-round detail
    # would be misleading.
    parsed.death_diagnostics = []
    parsed.scoreboard_meta = {
        "applied": True,
        "source": "demoparser2 scoreboard properties",
        "rounds": total_rounds,
        "event_layer_reliable": event_reliable,
        "kast_includes_traded": bool(event_reliable),
        "adr_source": "damage_total / total_rounds_played",
        "kast_source": "K/A/S round deltas from scoreboard totals (no traded rounds)",
        "swing_source": (
            "event stream"
            if event_reliable
            else "scoreboard round K/A/D fallback (approximate)"
        ),
    }
    return True
