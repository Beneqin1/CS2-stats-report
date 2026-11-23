"""Highlight-round detection and demo-goto payload for the HTML report.

Pure data in / data out: consumes a ParsedDemo-shaped object and emits plain
dicts.  Detection runs only on rounds whose event attribution matched the
scoreboard (matrix_coverage.reliable), so every card links to a round whose
kill events are trustworthy.  All labels are heuristic tags, not HLTV data.
"""

from __future__ import annotations

from typing import Any

TAG_LABELS = {
    "clutch": "残局翻盘",
    "multikill": "多杀",
    "spraydown": "瞬间连杀",
    "eco": "ECO 翻盘",
    "blitz": "速胜",
}

# A round is a clutch when the winner killed from a <=2-alive disadvantage.
_CLUTCH_MAX_ALIVE = 2
# 3+ kills by the same player inside this window counts as a spraydown.
_SPRAYDOWN_SECONDS = 8.0
_SPRAYDOWN_KILLS = 3
# Winner equipment value below this fraction of the loser's = eco upset.
_ECO_RATIO = 0.5
# First-kill-to-round-end within this many seconds = blitz round.
_BLITZ_SECONDS = 15.0


def _round_scores(rounds: list[Any], winner_of) -> dict[int, tuple[int, int]]:
    """Running score (team A wins, team B wins) after each formal round."""

    scores: dict[int, tuple[int, int]] = {}
    a_wins = b_wins = 0
    for item in rounds:
        winner = winner_of(item.index)
        if winner == "A":
            a_wins += 1
        elif winner == "B":
            b_wins += 1
        scores[item.index] = (a_wins, b_wins)
    return scores


def detect_highlights(parsed: Any) -> dict[str, Any]:
    """Detect highlight rounds and build the per-round timeline."""

    formal = [item for item in parsed.counted_rounds if item.is_formal]
    coverage = getattr(parsed, "matrix_coverage", {}) or {}
    excluded = set(coverage.get("excluded_rounds") or [])
    if coverage:
        reliable = {item.index for item in formal if item.round_num not in excluded}
    else:
        # No scoreboard cross-check (legacy path): trust every formal round.
        reliable = {item.index for item in formal}

    tickrate = float(getattr(parsed, "tickrate", 0.0)) or 64.0
    names = {int(p.steamid): p.name for p in parsed.players}
    team_of: dict[int, str] = getattr(parsed, "team_of", {}) or {}
    economy: dict[tuple[int, str], int] = getattr(parsed, "economy", {}) or {}
    scores = _round_scores(formal, parsed.winner_team_for)

    feed_by_round: dict[int, list[dict[str, Any]]] = {}
    for entry in getattr(parsed, "kill_feed", []) or []:
        if entry.get("bot_controlled"):
            continue
        feed_by_round.setdefault(entry.get("round_index"), []).append(entry)
    for key, entries in feed_by_round.items():
        entries = [item for item in entries if item.get("tick") is not None]
        entries.sort(key=lambda item: item["tick"])
        feed_by_round[key] = entries

    cards: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for item in formal:
        round_index = item.index
        winner = parsed.winner_team_for(round_index)
        winner_side = parsed.rounds[round_index].winner_side
        loser_side = {"T": "CT", "CT": "T"}.get(winner_side or "")
        entries = feed_by_round.get(round_index, [])
        a_wins, b_wins = scores.get(round_index, (0, 0))
        score_text = f"{a_wins}:{b_wins}"

        tags: list[str] = []
        notes: list[str] = []
        kills_by_player: dict[int, int] = {}
        is_reliable = round_index in reliable

        if is_reliable:
            enemy_kills: dict[int, list[dict[str, Any]]] = {}
            for entry in entries:
                attacker = entry.get("attacker")
                victim = entry.get("victim")
                if attacker is None or victim is None or attacker == victim:
                    continue
                if team_of.get(attacker) is None or team_of.get(attacker) == team_of.get(victim):
                    continue
                kills_by_player[attacker] = kills_by_player.get(attacker, 0) + 1
                enemy_kills.setdefault(attacker, []).append(entry)

            # Clutch: the winning team scored a kill from a <=2-alive deficit.
            best_clutch: tuple[int, int] | None = None
            for entry in entries:
                attacker = entry.get("attacker")
                if attacker is None or team_of.get(attacker) != winner:
                    continue
                a_alive = entry.get("attacker_alive")
                v_alive = entry.get("victim_alive")
                if a_alive is None or v_alive is None:
                    continue
                if 0 < a_alive <= _CLUTCH_MAX_ALIVE and v_alive > a_alive:
                    if best_clutch is None or (a_alive, -v_alive) < (best_clutch[0], -best_clutch[1]):
                        best_clutch = (a_alive, v_alive)
            if best_clutch is not None:
                tags.append("clutch")
                notes.append(f"{best_clutch[0]}v{best_clutch[1]} 残局获胜")

            # Multi-kill / spraydown by a single player.
            for sid, kills in enemy_kills.items():
                count = len(kills)
                if count >= 4:
                    tags.append("multikill")
                    notes.append(f"{names.get(sid, sid)} 单回合 {count} 杀")
                ticks = [k["tick"] for k in kills]
                for start in range(0, len(ticks) - _SPRAYDOWN_KILLS + 1):
                    span = (ticks[start + _SPRAYDOWN_KILLS - 1] - ticks[start]) / tickrate
                    if span <= _SPRAYDOWN_SECONDS:
                        tags.append("spraydown")
                        notes.append(
                            f"{names.get(sid, sid)} {span:.1f} 秒内 {_SPRAYDOWN_KILLS} 杀"
                        )
                        break

        # Eco upset and blitz only need economy/round timing, which come from
        # the authoritative round layer rather than the kill event stream.
        if winner_side and loser_side:
            win_value = economy.get((item.round_num, winner_side))
            lose_value = economy.get((item.round_num, loser_side))
            if win_value is not None and lose_value and lose_value > 0:
                if win_value < _ECO_RATIO * lose_value:
                    tags.append("eco")
                    notes.append(f"装备 ${win_value} vs ${lose_value}")

        first_kill_tick = next(
            (entry["tick"] for entry in entries if entry.get("attacker") is not None),
            None,
        )
        if first_kill_tick is not None and item.end_tick and item.end_tick > first_kill_tick:
            tail = (item.end_tick - first_kill_tick) / tickrate
            if tail <= _BLITZ_SECONDS:
                tags.append("blitz")
                notes.append(f"首杀后 {tail:.1f} 秒结束")

        deduped_tags = list(dict.fromkeys(tags))
        live_start = item.live_start or item.start_tick or item.end_tick
        if deduped_tags and live_start:
            top = sorted(kills_by_player.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            cards.append(
                {
                    "round_num": item.round_num,
                    "score": score_text,
                    "winner": winner or "",
                    "winner_side": winner_side or "",
                    "tags": [
                        {"key": key, "label": TAG_LABELS.get(key, key)} for key in deduped_tags
                    ],
                    "notes": notes,
                    "players": [
                        {"steamid": sid, "name": names.get(sid, str(sid)), "kills": count}
                        for sid, count in top
                    ],
                    "goto_tick": int(live_start),
                    "goto_time": _fmt_clock(int(live_start) / tickrate),
                    "reliable": is_reliable,
                }
            )
        timeline.append(
            {
                "round_num": item.round_num,
                "winner": winner or "",
                "winner_side": winner_side or "",
                "score": score_text,
                "highlight": bool(deduped_tags),
                "reliable": is_reliable,
                "goto_tick": int(live_start) if live_start else None,
            }
        )

    return {
        "cards": cards,
        "timeline": timeline,
        "demo_file": getattr(getattr(parsed, "path", None), "name", "") or "",
        "tickrate": tickrate,
        "tag_labels": dict(TAG_LABELS),
    }


def _fmt_clock(tick_seconds: float) -> str:
    total = max(0, int(tick_seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def goto_commands(demo_path: str, tick: int) -> dict[str, str]:
    """Console commands for jumping to a tick inside a local demo."""

    return {
        "playdemo": f'playdemo "{demo_path}"',
        "goto": f"demo_goto {tick}",
        "combined": f'playdemo "{demo_path}"; demo_goto {tick}',
    }
