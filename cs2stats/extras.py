"""Optional demoparser2 enrichments: utility usage and bot-takeover audit.

Everything here is additive: failures degrade to empty results plus a note in
``ParsedDemo.data_quality`` instead of breaking the parse.  The authoritative
K/D/A/ADR layer (scoreboard properties) never depends on this module.

Bot-takeover semantics
----------------------
In CS2, when a player takes over a bot, the bot's controller property
``m_bControllingBot`` flips to True on the *human's* controller entity and the
scoreboard credits the human with whatever happens while controlling.  We
sample that property inside every formal round.  Rounds with an active
takeover are recorded, and kill events involving the controller during those
rounds are flagged ``bot_controlled`` so event-derived features (matrix,
highlights, style DNA) can disclose or exclude them.  The scoreboard layer is
untouched: it matches what the in-game scoreboard shows by definition.
"""

from __future__ import annotations

import logging
from typing import Any

from .demo_parser import ParsedDemo, _as_int, _round_of_factory, normalize_side

LOGGER = logging.getLogger(__name__)

_UTILITY_EVENTS = {
    "flashbang_detonate": "flash",
    "smokegrenade_detonate": "smoke",
    "hegrenade_detonate": "he",
    "inferno_startburn": "molotov",
}

_TAKEOVER_PROPS = ["m_bControllingBot"]


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes"}


def collect_utility_usage(
    parsed: ParsedDemo,
    parser: Any | None = None,
) -> dict[int, dict[str, int]]:
    """Count grenade detonations per player inside formal rounds.

    ``parser`` may be an already-open demoparser2.DemoParser (the scoreboard
    layer passes its own to avoid parsing the header twice).
    """

    usage: dict[int, dict[str, int]] = {}
    try:
        if parser is None:
            import demoparser2

            parser = demoparser2.DemoParser(str(parsed.path))
        round_of = _round_of_factory(parsed.rounds)
        formal = {item.index for item in parsed.counted_rounds}
        for event, key in _UTILITY_EVENTS.items():
            try:
                frame = parser.parse_event(event)
            except Exception:
                continue
            records = frame.to_dict("records") if hasattr(frame, "to_dict") else []
            for record in records:
                round_index = round_of(record.get("tick"))
                if round_index is None or round_index not in formal:
                    continue
                sid = _as_int(record.get("user_steamid"), default=-1)
                if sid < 0:
                    continue
                usage.setdefault(sid, {"flash": 0, "smoke": 0, "he": 0, "molotov": 0})
                usage[sid][key] = usage[sid].get(key, 0) + 1
    except Exception as exc:
        LOGGER.warning("utility usage unavailable: %s", exc)
        return {}
    return usage


def detect_takeovers(
    parsed: ParsedDemo,
    parser: Any | None = None,
) -> dict[str, Any]:
    """Detect player-controls-bot spans via ``m_bControllingBot``.

    Returns an audit dict; also flags matching kill-feed entries with
    ``bot_controlled``.  Never raises.
    """

    audit: dict[str, Any] = {
        "check": "unavailable",
        "takeover_detected": False,
        "takeover_rounds": [],
        "controllers": {},
        "bot_controlled_events": 0,
    }
    try:
        if parser is None:
            import demoparser2

            parser = demoparser2.DemoParser(str(parsed.path))

        # Sample several points inside every formal round; a takeover usually
        # lasts whole rounds, so start/middle/end sampling catches it.
        ticks: list[int] = []
        tick_round: dict[int, int] = {}
        for item in parsed.counted_rounds:
            start = item.live_start
            end = item.end_tick or item.official_end_tick
            if not start or not end or end <= start:
                continue
            for sample in {int(start) + 8, (int(start) + int(end)) // 2, int(end) - 8}:
                if sample > 0:
                    ticks.append(sample)
                    tick_round[sample] = item.index
        if not ticks:
            audit["check"] = "no_ticks"
            return audit
        frame = parser.parse_ticks(_TAKEOVER_PROPS, ticks=sorted(set(ticks)))
        records = frame.to_dict("records") if hasattr(frame, "to_dict") else []
        if not records:
            return audit

        saw_value = False
        controllers: dict[int, set[int]] = {}
        for record in records:
            raw = record.get("m_bControllingBot")
            if raw is not None:
                saw_value = True
            if not _truthy(raw):
                continue
            sid = _as_int(record.get("steamid"), default=-1)
            round_index = tick_round.get(_as_int(record.get("tick")))
            if sid >= 0 and round_index is not None:
                controllers.setdefault(sid, set()).add(round_index)
        if not saw_value:
            # The property exists but is never populated: treat detection as
            # unsupported instead of claiming "no takeover".
            return audit
        audit["check"] = "ok"

        if not controllers:
            return audit
        round_nums = sorted(
            {parsed.rounds[index].round_num for rounds in controllers.values() for index in rounds}
        )
        names = {
            sid: next(
                (p.name for p in parsed.players if p.steamid == sid),
                str(sid),
            )
            for sid in controllers
        }
        flagged = 0
        for entry in parsed.kill_feed:
            if entry.get("bot_controlled"):
                continue
            round_index = entry.get("round_index")
            for sid, rounds in controllers.items():
                if round_index in rounds and sid in (entry.get("attacker"), entry.get("victim")):
                    entry["bot_controlled"] = True
                    flagged += 1
                    break
        audit.update(
            {
                "takeover_detected": True,
                "takeover_rounds": round_nums,
                "controllers": {str(sid): names[sid] for sid in controllers},
                "bot_controlled_events": flagged,
            }
        )
    except Exception as exc:
        LOGGER.warning("takeover detection unavailable: %s", exc)
        audit["check"] = "unavailable"
    return audit


def apply_extras(parsed: ParsedDemo, parser: Any | None = None) -> None:
    """Fill ``utility_usage`` and ``data_quality`` on *parsed* in place."""

    parsed.utility_usage = collect_utility_usage(parsed, parser)
    audit = detect_takeovers(parsed, parser)
    coverage = parsed.matrix_coverage or {}
    scoreboard = parsed.scoreboard_meta or {}
    audit["event_layer_reliable"] = bool(scoreboard.get("event_layer_reliable", True))
    audit["matrix_covered"] = int(coverage.get("covered", 0) or 0)
    audit["matrix_total"] = int(coverage.get("total", 0) or 0)
    parsed.data_quality = audit
    if audit.get("takeover_detected"):
        names = "、".join(audit["controllers"].values())
        parsed.side_warnings.append(
            f"检测到玩家接管 bot（{names}，回合 "
            f"{', '.join(str(n) for n in audit['takeover_rounds'])}）："
            f"接管时段的 {audit['bot_controlled_events']} 条击杀事件已标记为 bot_controlled，"
            "事件层衍生数据（Kill Matrix / 高光 / Style DNA）已按清洗后口径统计。"
        )
