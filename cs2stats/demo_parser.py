"""Parse a CS2 demo and rebuild the exact statistics needed by the report.

The authoritative numbers for the report (K/D/A, ADR, KAST, opening kills) are
read from ``awpy``'s ``demo.stats``.  This module rebuilds the same numbers from
the event tables so that the All / T / CT facets can be produced.  Rebuilding
also gives us the extra event-level values (headshot kills, flash assists,
traded deaths, multi-kills) and the data needed for the approximate layer.

If the rebuild does not agree with ``demo.stats`` the loader raises
``ReconstructionError`` instead of silently producing a mixed report.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import SIDE_CT, SIDE_T, SIDES
from .utils import clamp, safe_div, sha256_file

SIDE_NAMES = {SIDE_T: "terrorist", SIDE_CT: "counter-terrorist"}
SIDE_ALIASES = {
    "terrorist": SIDE_T,
    "terror": SIDE_T,
    "t": SIDE_T,
    "2": SIDE_T,
    "counter-terrorist": SIDE_CT,
    "counterterrorist": SIDE_CT,
    "counter_terrorist": SIDE_CT,
    "ct": SIDE_CT,
    "3": SIDE_CT,
}


class ParseError(RuntimeError):
    """Base class for demo parsing problems."""


class ReconstructionError(ParseError):
    """The event rebuild disagrees with awpy's own stats."""

    def __init__(self, message: str, rows: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.rows = rows or []


@dataclass
class Round:
    index: int
    round_num: int
    start_tick: int | None
    freeze_end_tick: int | None
    end_tick: int | None
    official_end_tick: int | None
    winner_side: str | None
    is_knife: bool
    reason: int = 0
    reason_name: str = ""

    @property
    def live_start(self) -> int | None:
        return self.start_tick or self.freeze_end_tick or self.end_tick

    @property
    def snapshot_tick(self) -> int | None:
        return self.freeze_end_tick or self.start_tick or self.end_tick

    @property
    def is_formal(self) -> bool:
        """True for a round that belongs in the official match statistics.

        Warm-up rounds, knife rounds, draws and synthetic game-start rows are
        excluded.  The round-level reason is authoritative when awpy provides
        it; requiring a winner keeps incomplete warm-up rows out as well.
        """
        if self.is_knife or self.round_num <= 0 or self.winner_side is None:
            return False
        reason = (self.reason_name or "").strip().casefold()
        if reason in {"draw", "game_start", "still_in_progress"}:
            return False
        if self.reason in {0, 10, 16}:
            return False
        return True


@dataclass
class Counters:
    rounds: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    flash_assists: int = 0
    headshot_kills: int = 0
    opening_kills: int = 0
    opening_deaths: int = 0
    traded_deaths: int = 0
    multikill_2k: int = 0
    multikill_3k: int = 0
    multikill_4k: int = 0
    multikill_5k: int = 0
    mvps: int = 0
    damage: int = 0
    utility_damage: int = 0
    kast_rounds: int = 0
    survived_rounds: int = 0
    multikill_rounds: int = 0
    kill_weight: float = 0.0
    death_weight: float = 0.0
    swing_sum: float = 0.0

    @property
    def kast(self) -> float:
        return safe_div(self.kast_rounds * 100.0, self.rounds)

    @property
    def adr(self) -> float:
        return safe_div(float(self.damage), self.rounds)

    @property
    def kpr(self) -> float:
        return safe_div(float(self.kills), self.rounds)

    @property
    def dpr(self) -> float:
        return safe_div(float(self.deaths), self.rounds)

    @property
    def apr(self) -> float:
        return safe_div(float(self.assists), self.rounds)

    @property
    def surviving(self) -> float:
        return safe_div(self.survived_rounds * 100.0, self.rounds)

    @property
    def multikills(self) -> int:
        return self.multikill_2k + self.multikill_3k + self.multikill_4k + self.multikill_5k


@dataclass
class Player:
    steamid: int
    name: str
    team_key: str
    exact: dict[str, Any]
    rounds_played: int
    raw: Counters = field(default_factory=Counters)
    stats_exact: dict[str, Any] = field(default_factory=dict)
    side: dict[str, Counters] = field(
        default_factory=lambda: {SIDE_T: Counters(), SIDE_CT: Counters()}
    )
    kill_rounds: dict[int, int] = field(default_factory=dict)
    assist_rounds: set[int] = field(default_factory=set)
    death_rounds: set[int] = field(default_factory=set)
    death_round_counts: dict[int, int] = field(default_factory=dict)
    traded_rounds: set[int] = field(default_factory=set)

    def exact_int(self, key: str) -> int:
        value = self.exact.get(key)
        return int(value or 0)

    def exact_float(self, key: str) -> float:
        value = self.exact.get(key)
        return float(value or 0.0)


@dataclass
class Team:
    key: str
    name: str
    color: str
    start_side: str
    players: list[Player] = field(default_factory=list)
    score: int = 0
    total_kills: int = 0
    avg_rating: float = 0.0
    wins_half: int = 0


@dataclass
class ParsedDemo:
    path: Path
    sha256: str
    map_name: str
    tickrate: float
    header: dict[str, Any]
    rounds: list[Round]
    counted_rounds: list[Round]
    players: list[Player]
    teams: dict[str, Team]
    team_of: dict[int, str]
    team_side: dict[tuple[int, str], str]
    economy: dict[tuple[int, str], int]
    matrix: dict[str, dict[str, dict[tuple[int, int], int]]]
    internal_rows: list[dict[str, Any]]
    side_warnings: list[str]
    stats_differences: list[dict[str, Any]] = field(default_factory=list)
    dropped_rounds: list[dict[str, Any]] = field(default_factory=list)
    death_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    field_availability: dict[int, dict[str, bool]] = field(default_factory=dict)
    scoreboard_meta: dict[str, Any] = field(default_factory=dict)
    # Raw kill events for formal rounds, kept so the scoreboard layer can
    # rebuild the Kill Matrix from only the rounds whose event attribution
    # matches the scoreboard deltas.  ``attacker`` is None for world/suicide
    # deaths.  Keys: round_index, tick, attacker, victim, weapon, headshot,
    # opening, attacker_alive, victim_alive (both pre-kill), t (seconds since
    # the round went live), attacker_pos, victim_pos, distance (metres),
    # bot_controlled (added by extras.py when a takeover is detected).
    kill_feed: list[dict[str, Any]] = field(default_factory=list)
    # scope -> victim side -> victim steamid -> count (world/suicide deaths)
    matrix_world: dict[str, dict[str, dict[int, int]]] = field(default_factory=dict)
    # {"covered": int, "total": int, "excluded_rounds": [int], "reason": str}
    matrix_coverage: dict[str, Any] = field(default_factory=dict)
    # Self-consistency findings (score sums, kill/death totals, ...).
    consistency_warnings: list[str] = field(default_factory=list)
    # Bot-takeover / event-layer quality summary, filled by extras.py.
    data_quality: dict[str, Any] = field(default_factory=dict)
    # Utility (grenade) usage per steamid: {"flash": n, "smoke": n, ...},
    # filled by extras.py.
    utility_usage: dict[int, dict[str, int]] = field(default_factory=dict)
    # Per formal round rule-based MVP (steamid), filled by scoreboard.py.
    round_mvps: dict[int, int] = field(default_factory=dict)
    # True when this object came from the on-disk parse cache.
    from_cache: bool = False

    def player_by_name(self, name: str) -> Player | None:
        needle = (name or "").strip().casefold()
        for player in self.players:
            if player.name.strip().casefold() == needle:
                return player
        return None

    def team_side_for(self, round_index: int, team_key: str) -> str | None:
        return self.team_side.get((round_index, team_key))

    def side_for(self, round_index: int, steamid: int) -> str | None:
        team_key = self.team_of.get(steamid)
        if team_key is None:
            return None
        return self.team_side_for(round_index, team_key)

    def winner_team_for(self, round_index: int) -> str | None:
        winner_side = self.rounds[round_index].winner_side
        if winner_side is None:
            return None
        for key in ("A", "B"):
            if self.team_side_for(round_index, key) == winner_side:
                return key
        return None

    @property
    def team_list(self) -> list[Team]:
        return [self.teams[key] for key in ("A", "B") if key in self.teams]

    @property
    def supports_awp_matrix(self) -> bool:
        return bool(self.matrix.get("awp"))


def normalize_side(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return SIDE_ALIASES.get(text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _is_enemy(attacker_side: str | None, victim_side: str | None) -> bool:
    """awpy treats an unknown side as an enemy (world/unresolved kills)."""
    if attacker_side is None or victim_side is None:
        return True
    return attacker_side != victim_side


def _is_confirmed_enemy(attacker_side: str | None, victim_side: str | None) -> bool:
    return attacker_side is not None and victim_side is not None and attacker_side != victim_side


def _utility_weapon(weapon: str | None) -> bool:
    text = (weapon or "").lower()
    return any(token in text for token in ("hegrenade", "inferno", "molotov", "incgrenade", "incendiary"))


def _round_of_factory(rounds: list[Round]):
    """Map a game tick to a formal round index, or None.

    Awpy's own helper only searches round starts; a tick after the final round
    (or after a round's end but before the next round) is then attributed to
    the last round.  That can pull warm-up/post-match deaths into the totals.
    We use both the live start and the first available end tick as bounds.
    """
    formal = [item for item in rounds if item.is_formal and item.live_start is not None]
    starts = []
    for item in formal:
        start = item.live_start
        if start is not None:
            starts.append((int(start), item.index))
    starts.sort(key=lambda pair: pair[0])
    start_values = [pair[0] for pair in starts]

    ends: dict[int, int] = {}
    for position, item in enumerate(formal):
        end = item.end_tick or item.official_end_tick
        if end is None and position + 1 < len(formal):
            next_start = formal[position + 1].live_start
            if next_start is not None:
                end = int(next_start) - 1
        if end is not None:
            ends[item.index] = int(end)

    freeze_windows = sorted(
        (int(item.start_tick), int(item.freeze_end_tick))
        for item in formal
        if item.start_tick is not None
        and item.freeze_end_tick is not None
        and item.start_tick < item.freeze_end_tick
    )
    freeze_starts = [pair[0] for pair in freeze_windows]

    def in_freeze(tick: int) -> bool:
        pos = bisect.bisect_right(freeze_starts, tick)
        return pos > 0 and tick < freeze_windows[pos - 1][1]

    def round_of(tick: int | None) -> int | None:
        if tick is None or not start_values:
            return None
        tick = int(tick)
        if tick < start_values[0] or in_freeze(tick):
            return None
        pos = bisect.bisect_right(start_values, tick) - 1
        if pos < 0:
            return None
        round_index = starts[pos][1]
        end = ends.get(round_index)
        if end is not None and tick > end:
            return None
        return round_index

    return round_of


def _parse_rounds(raw_rows: Iterable[dict[str, Any]]) -> list[Round]:
    rounds: list[Round] = []
    for index, row in enumerate(sorted(raw_rows, key=lambda item: _as_int(item.get("round_num")))):
        winner_side = normalize_side(row.get("winner_side"))
        if winner_side is None:
            winner_number = _as_int(row.get("winner"), default=-1)
            winner_side = SIDE_T if winner_number == 2 else SIDE_CT if winner_number == 3 else None
        rounds.append(
            Round(
                index=index,
                round_num=_as_int(row.get("round_num"), default=index + 1),
                start_tick=_as_int(row.get("start_tick"), default=0) or None,
                freeze_end_tick=_as_int(row.get("freeze_end_tick"), default=0) or None,
                end_tick=_as_int(row.get("end_tick"), default=0) or None,
                official_end_tick=_as_int(row.get("official_end_tick"), default=0) or None,
                winner_side=winner_side,
                is_knife=bool(row.get("is_knife_round")),
                reason=_as_int(row.get("reason"), default=0),
                reason_name=str(row.get("reason_name") or ""),
            )
        )
    return rounds


def _flag_unended_rounds(demo_path: Path, rounds: list[Round]) -> list[str]:
    """Flag rounds that went live but never ended (truncated recording).

    awpy builds its round table from round events.  When a GOTV recording
    stops mid-round (or a bot match emits sparse round events) the events for
    the trailing round are missing, and the report would otherwise look like a
    short but complete match.  ``round_freeze_end`` marks the moment a round
    goes live, so any freeze end that no parsed round claims — after the first
    parsed round, so pre-match warm-up freezes are ignored — is evidence of a
    round that started but never finished.  Such rounds are appended as
    non-formal rows (``winner_side=None`` / ``still_in_progress``), which keeps
    them out of every statistic while making the gap visible in the report.
    """
    if not rounds:
        return []
    try:
        from demoparser2 import DemoParser

        parsed_events = DemoParser(str(demo_path)).parse_events(["round_freeze_end"])
        freeze_ticks = sorted(
            {
                int(tick)
                for _, frame in parsed_events
                for tick in frame["tick"].tolist()
            }
        )
    except Exception:
        return []
    if not freeze_ticks:
        return []
    starts = [
        tick
        for tick in (item.start_tick or item.freeze_end_tick for item in rounds)
        if tick is not None
    ]
    if not starts:
        return []
    first_start = min(starts)
    covered = {item.freeze_end_tick for item in rounds if item.freeze_end_tick is not None}
    extra = [tick for tick in freeze_ticks if tick not in covered and tick > first_start]
    if not extra:
        return []
    next_num = max(item.round_num for item in rounds) + 1
    labels: list[str] = []
    for offset, tick in enumerate(extra):
        rounds.append(
            Round(
                index=len(rounds),
                round_num=next_num + offset,
                start_tick=tick,
                freeze_end_tick=tick,
                end_tick=None,
                official_end_tick=None,
                winner_side=None,
                is_knife=False,
                reason=0,
                reason_name="still_in_progress",
            )
        )
        labels.append(f"第 {next_num + offset} 回合（tick {tick}）")
    return [
        "demo 录制不完整：检测到 "
        f"{len(extra)} 个回合 freeze time 已结束、比赛已开打，但没有对应的回合结束记录——"
        "GOTV 录制在回合进行中停止。这些回合未计入任何统计："
        + "、".join(labels)
        + "。如果实际对局远不止这些回合，说明 .dem 文件本身只录制了对局的一部分"
        "（录制中断或提前停止），解析无法还原未被录制的回合。"
    ]


def _clean_player_name(value: Any) -> str:
    text = str(value or "").strip()
    # Keep Unicode and visible punctuation; only drop control characters and
    # collapse whitespace introduced by console/chat formatting.
    text = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32)
    return " ".join(text.split())


def _looks_like_steamid(value: str) -> bool:
    text = value.strip()
    return bool(text) and text.isdigit() and len(text) >= 15


def _display_player_name(
    stats_row: dict[str, Any],
    players_by_sid: dict[int, dict[str, Any]],
    steamid: int,
) -> str:
    """Prefer the player-table nickname, then the stats-table nickname."""
    stats_name = _clean_player_name(stats_row.get("name"))
    players_name = _clean_player_name((players_by_sid.get(steamid) or {}).get("name"))
    if players_name and players_name.casefold() not in {"unknown", "unnamed"}:
        return players_name
    if stats_name and not _looks_like_steamid(stats_name):
        return stats_name
    if players_name:
        return players_name
    if stats_name:
        return stats_name
    return f"Player {steamid}"


def _snapshot_sides(demo: Any, rounds: list[Round]) -> dict[tuple[int, int], str]:
    ticks = sorted({int(item.snapshot_tick) for item in rounds if item.snapshot_tick is not None})
    if not ticks:
        return {}
    try:
        frame = demo.snapshots(ticks=ticks)
    except Exception:
        return {}
    result: dict[tuple[int, int], str] = {}
    for row in frame.to_dicts():
        tick = _as_int(row.get("tick"), default=-1)
        steamid = _as_int(row.get("steamid"), default=-1)
        side = normalize_side(row.get("side"))
        if tick >= 0 and steamid >= 0 and side in SIDES:
            result[(tick, steamid)] = side
    return result


def _assign_teams(
    stats_rows: list[dict[str, Any]],
    players_rows: list[dict[str, Any]],
    rounds: list[Round],
    snap: dict[tuple[int, int], str],
    kills: list[dict[str, Any]],
    damages: list[dict[str, Any]],
    blinds: list[dict[str, Any]],
    round_of,
) -> tuple[dict[int, str], str, str, list[str]]:
    """Return (steamid -> team key, A start side, B start side, warnings)."""
    warnings: list[str] = []
    steamids = [_as_int(row.get("steamid")) for row in stats_rows]
    sid_set = set(steamids)

    # 1) Use the first formal round whose freeze-time snapshot covers every player.
    for round_item in rounds:
        if not round_item.is_formal:
            continue
        tick = round_item.snapshot_tick
        if tick is None:
            continue
        sides = {sid: snap.get((int(tick), sid)) for sid in steamids}
        if all(side in SIDES for side in sides.values()):
            groups = {
                SIDE_T: [sid for sid, side in sides.items() if side == SIDE_T],
                SIDE_CT: [sid for sid, side in sides.items() if side == SIDE_CT],
            }
            if groups[SIDE_T] and groups[SIDE_CT]:
                t_set, ct_set = set(groups[SIDE_T]), set(groups[SIDE_CT])
                mapping = {sid: ("A" if sid in t_set else "B") for sid in steamids}
                return mapping, SIDE_T, SIDE_CT, warnings

    # 2) Fall back to the last observed side in demo.players.
    player_sides: dict[int, str] = {}
    for row in players_rows:
        sid = _as_int(row.get("steamid"))
        side = normalize_side(row.get("side"))
        if sid in sid_set and side in SIDES:
            player_sides[sid] = side
    t_set = {sid for sid, side in player_sides.items() if side == SIDE_T}
    ct_set = {sid for sid, side in player_sides.items() if side == SIDE_CT}
    if not t_set or not ct_set:
        # 3) Last resort: use any snapshot, then event sides.
        for round_item in rounds:
            tick = round_item.snapshot_tick
            if tick is None:
                continue
            for sid in steamids:
                side = snap.get((int(tick), sid))
                if side in SIDES and sid not in t_set and sid not in ct_set:
                    (t_set if side == SIDE_T else ct_set).add(sid)
            if t_set and ct_set:
                break
    if not t_set or not ct_set:
        raise ParseError(
            "无法从 demo 的 snapshot / 队伍信息判断双方选手，无法生成 T/CT 分面。"
        )
    for sid in steamids:
        if sid not in t_set and sid not in ct_set:
            warnings.append(f"选手 {sid} 无法确定队伍，按 B 队处理。")
            ct_set.add(sid)
    mapping = {sid: ("A" if sid in t_set else "B") for sid in steamids}
    return mapping, SIDE_T, SIDE_CT, warnings


def _team_names(
    stats_rows: list[dict[str, Any]],
    players_rows: list[dict[str, Any]],
    members: set[int],
    fallback: str,
) -> str:
    names: list[str] = []
    for row in players_rows:
        if _as_int(row.get("steamid")) not in members:
            continue
        raw = row.get("team_clan_name")
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            names.append(text)
    if not names:
        for row in stats_rows:
            if _as_int(row.get("steamid")) not in members:
                continue
            raw = row.get("team_clan_name")
            if raw is not None and str(raw).strip():
                names.append(str(raw).strip())
    if not names:
        return fallback
    return Counter(names).most_common(1)[0][0]


def _event_side_counts(
    rounds: list[Round],
    team_of: dict[int, str],
    kills: list[dict[str, Any]],
    damages: list[dict[str, Any]],
    blinds: list[dict[str, Any]],
    round_of,
) -> dict[tuple[int, str], Counter]:
    counts: dict[tuple[int, str], Counter] = defaultdict(Counter)
    for row in kills:
        round_index = round_of(row.get("tick"))
        if round_index is None:
            continue
        for prefix in ("attacker", "victim"):
            sid = _as_int(row.get(f"{prefix}_steamid"), default=-1)
            side = normalize_side(row.get(f"{prefix}_side"))
            team_key = team_of.get(sid)
            if team_key and side in SIDES:
                counts[(round_index, team_key)][side] += 1
    for row in damages:
        round_index = round_of(row.get("tick"))
        if round_index is None:
            continue
        for prefix in ("attacker", "victim"):
            sid = _as_int(row.get(f"{prefix}_steamid"), default=-1)
            side = normalize_side(row.get(f"{prefix}_side"))
            team_key = team_of.get(sid)
            if team_key and side in SIDES:
                counts[(round_index, team_key)][side] += 0.5
    for row in blinds:
        round_index = round_of(row.get("tick"))
        if round_index is None:
            continue
        for prefix in ("attacker", "victim"):
            sid = _as_int(row.get(f"{prefix}_steamid"), default=-1)
            side = normalize_side(row.get(f"{prefix}_side"))
            team_key = team_of.get(sid)
            if team_key and side in SIDES:
                counts[(round_index, team_key)][side] += 0.25
    return counts


def _build_team_sides(
    rounds: list[Round],
    team_of: dict[int, str],
    snap: dict[tuple[int, int], str],
    kills: list[dict[str, Any]],
    damages: list[dict[str, Any]],
    blinds: list[dict[str, Any]],
    round_of,
) -> dict[tuple[int, str], str]:
    members = {key: {sid for sid, team in team_of.items() if team == key} for key in ("A", "B")}
    event_counts = _event_side_counts(rounds, team_of, kills, damages, blinds, round_of)
    result: dict[tuple[int, str], str] = {}

    for round_item in rounds:
        tick = round_item.snapshot_tick
        for key in ("A", "B"):
            side = None
            if tick is not None:
                sides = {
                    snap[(int(tick), sid)]
                    for sid in members[key]
                    if (int(tick), sid) in snap
                }
                if len(sides) == 1:
                    side = next(iter(sides))
            if side is None and event_counts.get((round_item.index, key)):
                side = event_counts[(round_item.index, key)].most_common(1)[0][0]
            result[(round_item.index, key)] = side

    for key in ("A", "B"):
        last = None
        for round_item in rounds:
            current = result.get((round_item.index, key))
            if current in SIDES:
                last = current
            elif last:
                result[(round_item.index, key)] = last
        last = None
        for round_item in reversed(rounds):
            current = result.get((round_item.index, key))
            if current in SIDES:
                last = current
            elif last:
                result[(round_item.index, key)] = last

    for round_item in rounds:
        a_side = result.get((round_item.index, "A"))
        b_side = result.get((round_item.index, "B"))
        if a_side not in SIDES and b_side in SIDES:
            result[(round_item.index, "A")] = SIDE_CT if b_side == SIDE_T else SIDE_T
        elif b_side not in SIDES and a_side in SIDES:
            result[(round_item.index, "B")] = SIDE_CT if a_side == SIDE_T else SIDE_T
        elif a_side in SIDES and b_side in SIDES and a_side == b_side:
            raise ParseError(
                f"第 {round_item.round_num} 回合双方阵营被判定为同一侧 ({a_side})，无法生成 T/CT 分面。"
            )
        if result.get((round_item.index, "A")) not in SIDES or result.get((round_item.index, "B")) not in SIDES:
            raise ParseError(
                f"无法确定第 {round_item.round_num} 回合双方阵营，无法生成 T/CT 分面。"
            )
    return {key: value for key, value in result.items() if value in SIDES}


def _mark_trades(kills: list[dict[str, Any]], tickrate: float, round_of=None) -> None:
    for row in kills:
        row["_victim_traded"] = False
        row["_is_trade"] = False
    if not kills:
        return
    order = sorted(range(len(kills)), key=lambda index: _as_int(kills[index].get("tick")))
    trade_ticks = int(round(5.25 * tickrate))
    for position, death_index in enumerate(order):
        death = kills[death_index]
        death_tick = _as_int(death.get("tick"))
        death_round = round_of(death_tick) if round_of is not None else None
        if round_of is not None and death_round is None:
            continue
        killer = death.get("attacker_steamid")
        victim_side = normalize_side(death.get("victim_side"))
        if killer is None or victim_side is None:
            continue
        for revenge_index in order[position + 1 :]:
            revenge = kills[revenge_index]
            revenge_tick = _as_int(revenge.get("tick"))
            if revenge_tick - death_tick > trade_ticks:
                break
            if revenge_tick <= death_tick:
                continue
            if round_of is not None and round_of(revenge_tick) != death_round:
                continue
            if revenge.get("_is_trade"):
                continue
            if _as_int(revenge.get("victim_steamid"), default=-1) != _as_int(killer, default=-1):
                continue
            if normalize_side(revenge.get("attacker_side")) != victim_side:
                continue
            death["_victim_traded"] = True
            revenge["_is_trade"] = True
            break


def _mark_opening(kills: list[dict[str, Any]], round_of) -> None:
    seen: set[int] = set()
    for row in kills:
        row["_opening"] = False
        round_index = round_of(row.get("tick"))
        if round_index is None or round_index in seen:
            continue
        seen.add(round_index)
        row["_opening"] = True


def _exact_from_raw(raw: Counters, total_rounds: int) -> dict[str, Any]:
    """Expose the formal-round event rebuild through the exact-field interface."""
    headshot_pct = safe_div(raw.headshot_kills * 100.0, raw.kills)
    return {
        "rounds_played": total_rounds,
        "kills": raw.kills,
        "deaths": raw.deaths,
        "assists": raw.assists,
        "flash_assists": raw.flash_assists,
        "headshot_kills": raw.headshot_kills,
        "headshot_pct": headshot_pct,
        "opening_kills": raw.opening_kills,
        "opening_deaths": raw.opening_deaths,
        "traded_deaths": raw.traded_deaths,
        "multikill_2k": raw.multikill_2k,
        "multikill_3k": raw.multikill_3k,
        "multikill_4k": raw.multikill_4k,
        "multikill_5k": raw.multikill_5k,
        "utility_damage": raw.utility_damage,
        "adr": raw.adr,
        "kast": raw.kast,
    }


def _internal_check(players: list[Player], total_rounds: int) -> list[dict[str, Any]]:
    integer_fields = (
        "kills",
        "deaths",
        "assists",
        "flash_assists",
        "headshot_kills",
        "opening_kills",
        "opening_deaths",
        "traded_deaths",
        "multikill_2k",
        "multikill_3k",
        "multikill_4k",
        "multikill_5k",
        "utility_damage",
    )
    rows: list[dict[str, Any]] = []
    for player in players:
        source_exact = player.stats_exact or player.exact
        for name in integer_fields:
            source = int(source_exact.get(name) or 0)
            rebuilt = int(getattr(player.raw, name))
            if source != rebuilt:
                rows.append(
                    {
                        "player": player.name,
                        "field": name,
                        "source": source,
                        "rebuilt": rebuilt,
                        "delta": rebuilt - source,
                    }
                )
        for name in ("kast", "adr"):
            source = float(source_exact.get(name) or 0.0)
            rebuilt = float(getattr(player.raw, name))
            if abs(source - rebuilt) > 1e-3:
                rows.append(
                    {
                        "player": player.name,
                        "field": name,
                        "source": round(source, 4),
                        "rebuilt": round(rebuilt, 4),
                        "delta": round(rebuilt - source, 4),
                    }
                )
        source_rounds = int(source_exact.get("rounds_played") or 0)
        if source_rounds and source_rounds != total_rounds:
            rows.append(
                {
                    "player": player.name,
                    "field": "rounds_played",
                    "source": source_rounds,
                    "rebuilt": total_rounds,
                    "delta": total_rounds - source_rounds,
                }
            )
    return rows


def _side_consistency(player: Player) -> list[str]:
    warnings: list[str] = []
    for field_name in (
        "kills",
        "deaths",
        "assists",
        "flash_assists",
        "headshot_kills",
        "opening_kills",
        "opening_deaths",
        "traded_deaths",
        "multikill_2k",
        "multikill_3k",
        "multikill_4k",
        "multikill_5k",
        "damage",
        "utility_damage",
        "kast_rounds",
    ):
        total = int(getattr(player.raw, field_name))
        side_total = int(getattr(player.side[SIDE_T], field_name)) + int(
            getattr(player.side[SIDE_CT], field_name)
        )
        if total != side_total:
            warnings.append(
                f"{player.name}: {field_name} 全场={total}，T+CT={side_total}（分面不一致）"
            )
    return warnings


def load_demo(path: str | Path, *, check_internal: bool = True) -> ParsedDemo:
    """Load a demo and return the Python-only model used by the report."""
    demo_path = Path(path).expanduser().resolve()
    if not demo_path.is_file():
        raise ParseError(f"找不到 demo 文件：{demo_path}")
    if demo_path.suffix.lower() != ".dem":
        raise ParseError(f"文件不是 .dem：{demo_path.name}")

    from .cache import load_cached, store
    from .utils import sha256_file as _sha256

    try:
        digest = _sha256(demo_path)
    except OSError as exc:
        raise ParseError(f"无法读取 demo 文件：{demo_path}（{exc}）") from exc
    cached = load_cached(demo_path, digest)
    if cached is not None:
        return cached

    try:
        from awpy import Demo
    except Exception as exc:  # pragma: no cover - packaging / install problem
        raise ParseError(
            "无法导入 awpy。请确认已安装 awpy（见 README.txt），或在打包后的程序中使用。"
        ) from exc

    try:
        demo = Demo(str(demo_path))
        stats_frame = demo.stats
        stats_rows = stats_frame.to_dicts()
        rounds_rows = demo.rounds.to_dicts()
        kills = list(demo.kills.to_dicts())
        damages = list(demo.damages.to_dicts())
        blinds = list(demo.blinds.to_dicts())
        economy_rows = demo.round_economy.to_dicts()
        players_rows = list(demo.players.to_dicts())
        tickrate = _as_float(demo.tick_rate, 64.0) or 64.0
        header = dict(demo.header or {})
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"awpy 读取 demo 失败：{exc}") from exc

    if not stats_rows:
        raise ParseError("demo.stats 为空：该 demo 可能不是完整的比赛，或 awpy 无法识别。")

    rounds = _parse_rounds(rounds_rows)
    recording_warnings = _flag_unended_rounds(demo_path, rounds)
    counted_rounds = [item for item in rounds if item.is_formal]
    dropped_rounds = [
        {
            "round_num": item.round_num,
            "reason": item.reason,
            "reason_name": item.reason_name,
            "is_knife": item.is_knife,
            "winner_side": item.winner_side,
        }
        for item in rounds
        if not item.is_formal
    ]
    if not counted_rounds:
        raise ParseError(
            "demo 中没有可统计的正式回合（热身、knife、平局或未完成回合已全部排除）。"
        )
    round_of = _round_of_factory(rounds)

    snap = _snapshot_sides(demo, rounds)
    team_of, a_start_side, b_start_side, team_warnings = _assign_teams(
        stats_rows, players_rows, rounds, snap, kills, damages, blinds, round_of
    )
    team_side = _build_team_sides(
        counted_rounds, team_of, snap, kills, damages, blinds, round_of
    )

    # Team names / colors.
    team_members = {
        key: {sid for sid, team in team_of.items() if team == key} for key in ("A", "B")
    }
    team_names = {
        "A": _team_names(stats_rows, players_rows, team_members["A"], "Team A"),
        "B": _team_names(stats_rows, players_rows, team_members["B"], "Team B"),
    }
    if team_names["A"] == team_names["B"]:
        team_names["B"] = "Team B"
    if not any(row.get("team_clan_name") for row in players_rows):
        team_names = {"A": "Team A", "B": "Team B"}

    # Economy lookup keyed by (round_num, side).
    economy: dict[tuple[int, str], int] = {}
    for row in economy_rows:
        side = normalize_side(row.get("side"))
        if side in SIDES:
            economy[(_as_int(row.get("round_num")), side)] = _as_int(row.get("equipment_value"))

    # Trade and opening flags must be set before the kill loop.
    _mark_trades(kills, tickrate, round_of)
    _mark_opening(kills, round_of)

    players_by_sid = {
        _as_int(row.get("steamid")): row
        for row in players_rows
        if _as_int(row.get("steamid"), default=-1) >= 0
    }
    players: list[Player] = []
    for row in stats_rows:
        sid = _as_int(row.get("steamid"))
        players.append(
            Player(
                steamid=sid,
                name=_display_player_name(row, players_by_sid, sid),
                team_key=team_of[sid],
                exact=row,
                rounds_played=_as_int(row.get("rounds_played")),
            )
        )
    by_sid = {player.steamid: player for player in players}
    by_name = {player.name.casefold(): player for player in players}

    # Helpers for side attribution.  Fall back to the event side if the
    # per-round snapshot was unavailable.
    def side_of_player(round_index: int, steamid: int, fallback: str | None = None) -> str | None:
        team_key = team_of.get(steamid)
        if team_key is not None:
            side = team_side.get((round_index, team_key))
            if side in SIDES:
                return side
        return fallback if fallback in SIDES else None

    def round_num(round_index: int) -> int:
        return rounds[round_index].round_num

    # Alive counts are per round and per team, used by the swing proxy.
    alive: dict[int, dict[str, int]] = {}
    for round_item in counted_rounds:
        alive[round_item.index] = {
            "A": len(team_members["A"]),
            "B": len(team_members["B"]),
        }

    # Kills in tick order; the tables are normally sorted already, but the
    # order matters for the health chain and the trade scan.
    kills_sorted = sorted(enumerate(kills), key=lambda pair: (_as_int(pair[1].get("tick")), pair[0]))
    opening_seen: set[int] = set()
    kill_feed: list[dict[str, Any]] = []
    matrix: dict[str, dict[str, dict[tuple[int, int], int]]] = {}
    for original_index, kill in kills_sorted:
        round_index = round_of(kill.get("tick"))
        if round_index is None or not rounds[round_index].is_formal:
            continue
        attacker = _as_int(kill.get("attacker_steamid"), default=-1)
        victim = _as_int(kill.get("victim_steamid"), default=-1)
        attacker_side_raw = normalize_side(kill.get("attacker_side"))
        victim_side_raw = normalize_side(kill.get("victim_side"))
        attacker_obj = by_sid.get(attacker)
        victim_obj = by_sid.get(victim)
        attacker_team = team_of.get(attacker)
        victim_team = team_of.get(victim)
        attacker_side = side_of_player(round_index, attacker, attacker_side_raw) if attacker >= 0 else attacker_side_raw
        victim_side = side_of_player(round_index, victim, victim_side_raw) if victim >= 0 else victim_side_raw

        # Keep every formal-round death for the scoreboard-gated Kill Matrix
        # rebuild.  World/self kills have no attacker player; they are stored
        # with attacker=None instead of being dropped.
        if victim_obj is not None:
            team_alive_now = alive.setdefault(
                round_index,
                {"A": len(team_members["A"]), "B": len(team_members["B"])},
            )
            live_start = rounds[round_index].freeze_end_tick or rounds[round_index].live_start
            entry: dict[str, Any] = {
                "round_index": round_index,
                "tick": _as_int(kill.get("tick")),
                "attacker": (
                    attacker
                    if attacker >= 0 and attacker != victim and attacker_obj is not None
                    else None
                ),
                "victim": victim,
                "weapon": str(kill.get("weapon") or ""),
                "headshot": bool(kill.get("headshot")),
                "opening": bool(kill.get("_opening")),
                # Alive counts *before* this kill (used for clutch detection).
                "attacker_alive": (
                    team_alive_now.get(attacker_team)
                    if attacker_team is not None
                    else None
                ),
                "victim_alive": (
                    team_alive_now.get(victim_team)
                    if victim_team is not None
                    else None
                ),
                # Seconds since the round went live (freeze time excluded).
                "t": round(
                    (_as_int(kill.get("tick")) - _as_int(live_start)) / tickrate, 2
                )
                if live_start
                else None,
            }
            for prefix in ("attacker", "victim"):
                pos = [
                    _as_float(kill.get(f"{prefix}_{axis}"), default=float("nan"))
                    for axis in ("x", "y", "z")
                ]
                entry[f"{prefix}_pos"] = pos
            attacker_pos = entry["attacker_pos"]
            victim_pos = entry["victim_pos"]
            if entry["attacker"] is not None and all(
                math.isfinite(v) for v in attacker_pos + victim_pos
            ):
                entry["distance"] = round(
                    math.dist(attacker_pos, victim_pos) / 39.3701, 1
                )  # game units -> metres
            else:
                entry["distance"] = None
            kill_feed.append(entry)

        # Swing proxy: measure the man advantage before this kill.
        if (
            attacker_team is not None
            and victim_team is not None
            and attacker_team != victim_team
            and attacker_obj is not None
            and attacker != victim
        ):
            team_alive = alive.setdefault(
                round_index,
                {"A": len(team_members["A"]), "B": len(team_members["B"])},
            )
            a_alive = max(0, team_alive.get(attacker_team, 0))
            v_alive = max(0, team_alive.get(victim_team, 0))
            difference = a_alive - v_alive
            if difference < 0:
                leverage = float(-difference)
            elif difference == 0:
                leverage = 0.75
            else:
                leverage = -0.35 * difference
            winner_team = None
            winner_side = rounds[round_index].winner_side
            if winner_side in SIDES:
                for key in ("A", "B"):
                    if team_side.get((round_index, key)) == winner_side:
                        winner_team = key
                        break
            if winner_team is not None and attacker_team != winner_team:
                leverage = min(leverage, 0.0)
            attacker_obj.raw.swing_sum += leverage
            side_counter = attacker_obj.side.get(attacker_side or "")
            if side_counter is not None:
                side_counter.swing_sum += leverage

        # The victim always counts as a death and is removed from the alive
        # count, including team-kills and suicides.
        if victim_team is not None:
            team_alive = alive.setdefault(
                round_index,
                {"A": len(team_members["A"]), "B": len(team_members["B"])},
            )
            team_alive[victim_team] = max(0, team_alive.get(victim_team, 0) - 1)

        kill_is_enemy = attacker != victim and attacker >= 0 and _is_enemy(attacker_side_raw, victim_side_raw)
        if kill_is_enemy and attacker_obj is not None:
            attacker_obj.raw.kills += 1
            attacker_obj.kill_rounds[round_index] = attacker_obj.kill_rounds.get(round_index, 0) + 1
            if bool(kill.get("headshot")):
                attacker_obj.raw.headshot_kills += 1
            side_counter = attacker_obj.side.get(attacker_side or "")
            if side_counter is not None:
                side_counter.kills += 1
                if bool(kill.get("headshot")):
                    side_counter.headshot_kills += 1

            # Economy weight for an enemy kill.
            weight = 1.0
            if attacker_team is not None:
                attack_side = team_side.get((round_index, attacker_team)) or attacker_side
                victim_side_for_econ = (
                    team_side.get((round_index, victim_team)) if victim_team is not None else victim_side
                ) or victim_side
                attack_value = economy.get((round_num(round_index), attack_side or ""), 0)
                victim_value = economy.get((round_num(round_index), victim_side_for_econ or ""), 0)
                if attack_value > 0 and victim_value > 0:
                    ratio = attack_value / max(1, victim_value)
                    weight = clamp(1.0 + 0.30 * (1.0 - ratio), 0.70, 1.45)
            attacker_obj.raw.kill_weight += weight
            side_counter = attacker_obj.side.get(attacker_side or "")
            if side_counter is not None:
                side_counter.kill_weight += weight

        if victim_obj is not None:
            victim_obj.raw.deaths += 1
            victim_obj.death_rounds.add(round_index)
            victim_obj.death_round_counts[round_index] = (
                victim_obj.death_round_counts.get(round_index, 0) + 1
            )
            if bool(kill.get("_victim_traded")):
                victim_obj.raw.traded_deaths += 1
                victim_obj.traded_rounds.add(round_index)
            side_counter = victim_obj.side.get(victim_side or "")
            if side_counter is not None:
                side_counter.deaths += 1
                if bool(kill.get("_victim_traded")):
                    side_counter.traded_deaths += 1

            death_weight = 1.0
            if kill_is_enemy and attacker_team is not None and victim_team is not None:
                victim_side_for_econ = team_side.get((round_index, victim_team)) or victim_side
                attacker_side_for_econ = team_side.get((round_index, attacker_team)) or attacker_side
                victim_value = economy.get((round_num(round_index), victim_side_for_econ or ""), 0)
                attacker_value = economy.get((round_num(round_index), attacker_side_for_econ or ""), 0)
                if victim_value > 0 and attacker_value > 0:
                    ratio = victim_value / max(1, attacker_value)
                    death_weight = clamp(1.0 + 0.15 * (ratio - 1.0), 0.85, 1.15)
            victim_obj.raw.death_weight += death_weight
            side_counter = victim_obj.side.get(victim_side or "")
            if side_counter is not None:
                side_counter.death_weight += death_weight

        # Assists: the game's recorded assister plus active flash throwers.
        assisters: dict[int, tuple[str | None, bool]] = {}
        recorded_assister = _as_int(kill.get("assister_steamid"), default=-1)
        if recorded_assister >= 0:
            recorded_side = normalize_side(kill.get("assister_side"))
            if (
                recorded_assister != attacker
                and recorded_assister != victim
                and not (recorded_side is not None and victim_side_raw is not None and recorded_side == victim_side_raw)
            ):
                assisters[recorded_assister] = (
                    kill.get("assister_name"),
                    bool(kill.get("assist_flash")),
                )

        if (
            victim >= 0
            and attacker >= 0
            and attacker != victim
            and _is_confirmed_enemy(attacker_side_raw, victim_side_raw)
        ):
            for blind in blinds:
                if _as_int(blind.get("victim_steamid"), default=-1) != victim:
                    continue
                thrower = _as_int(blind.get("attacker_steamid"), default=-1)
                if thrower < 0:
                    continue
                blind_round = round_of(blind.get("tick"))
                if blind_round != round_index:
                    continue
                elapsed = (_as_int(kill.get("tick")) - _as_int(blind.get("tick"))) / tickrate
                duration = _as_float(blind.get("duration"), 0.0)
                if elapsed < 0.0 or elapsed >= duration:
                    continue
                different_pawn = False
                if (
                    kill.get("victim_entity_id") is not None
                    and kill.get("victim_entity_serial") is not None
                    and blind.get("victim_entity_id") is not None
                    and blind.get("victim_entity_serial") is not None
                ):
                    different_pawn = (
                        _as_int(kill.get("victim_entity_id")),
                        _as_int(kill.get("victim_entity_serial")),
                    ) != (
                        _as_int(blind.get("victim_entity_id")),
                        _as_int(blind.get("victim_entity_serial")),
                    )
                if different_pawn:
                    continue
                if thrower == attacker or thrower == victim:
                    continue
                if normalize_side(blind.get("attacker_side")) != attacker_side_raw:
                    continue
                if thrower in assisters:
                    old_name, _ = assisters[thrower]
                    assisters[thrower] = (old_name or blind.get("attacker_name"), True)
                else:
                    assisters[thrower] = (blind.get("attacker_name"), True)

        for assister, (_, is_flash) in assisters.items():
            assister_obj = by_sid.get(assister)
            if assister_obj is None:
                continue
            assister_obj.raw.assists += 1
            assister_obj.assist_rounds.add(round_index)
            if is_flash:
                assister_obj.raw.flash_assists += 1
            side_counter = assister_obj.side.get(attacker_side or "")
            if side_counter is not None:
                side_counter.assists += 1
                if is_flash:
                    side_counter.flash_assists += 1

        # Opening kill: the first kill of the round, even if it is a team-kill
        # or a suicide (awpy uses the same rule).
        if round_index not in opening_seen:
            opening_seen.add(round_index)
            if attacker_obj is not None and attacker != victim:
                attacker_obj.raw.opening_kills += 1
                side_counter = attacker_obj.side.get(attacker_side or "")
                if side_counter is not None:
                    side_counter.opening_kills += 1
            if victim_obj is not None:
                victim_obj.raw.opening_deaths += 1
                side_counter = victim_obj.side.get(victim_side or "")
                if side_counter is not None:
                    side_counter.opening_deaths += 1

    # Damage / ADR, following awpy's health-chain + per-hit cap algorithm.
    victim_health: dict[tuple[int, int], int] = {}
    for damage in damages:
        round_index = round_of(damage.get("tick"))
        if round_index is None or not rounds[round_index].is_formal:
            continue
        victim = _as_int(damage.get("victim_steamid"), default=-1)
        attacker = _as_int(damage.get("attacker_steamid"), default=-1)
        health_post = _as_int(damage.get("health_post"), default=-1)
        if victim >= 0:
            key = (round_index, victim)
            before = victim_health.get(key, 100)
            victim_health[key] = health_post if health_post >= 0 else before
        else:
            before = _as_int(damage.get("health_pre"), default=0)
        removed = min(max(0, _as_int(damage.get("dmg_health"), default=0)), max(0, before))
        if attacker < 0 or attacker == victim:
            continue
        attacker_side = normalize_side(damage.get("attacker_side"))
        victim_side = normalize_side(damage.get("victim_side"))
        attacker_side = side_of_player(round_index, attacker, attacker_side) if attacker >= 0 else attacker_side
        if not _is_confirmed_enemy(
            normalize_side(damage.get("attacker_side")),
            normalize_side(damage.get("victim_side")),
        ):
            continue
        attacker_obj = by_sid.get(attacker)
        if attacker_obj is None:
            continue
        attacker_obj.raw.damage += removed
        if _utility_weapon(damage.get("weapon")):
            attacker_obj.raw.utility_damage += removed
        side_counter = attacker_obj.side.get(attacker_side or "")
        if side_counter is not None:
            side_counter.damage += removed
            if _utility_weapon(damage.get("weapon")):
                side_counter.utility_damage += removed

    total_rounds = len(counted_rounds)
    for player in players:
        player.stats_exact = dict(player.exact)
        player.raw.rounds = total_rounds
        player.raw.kast_rounds = 0
        player.raw.survived_rounds = 0
        for round_item in counted_rounds:
            round_index = round_item.index
            if (
                player.kill_rounds.get(round_index)
                or round_index in player.assist_rounds
                or round_index not in player.death_rounds
                or round_index in player.traded_rounds
            ):
                player.raw.kast_rounds += 1
            if round_index not in player.death_rounds:
                player.raw.survived_rounds += 1
        for count in player.kill_rounds.values():
            if count == 2:
                player.raw.multikill_2k += 1
            elif count == 3:
                player.raw.multikill_3k += 1
            elif count == 4:
                player.raw.multikill_4k += 1
            elif count >= 5:
                player.raw.multikill_5k += 1
        player.raw.multikill_rounds = sum(
            1 for count in player.kill_rounds.values() if count >= 2
        )

    # Rule-based round MVP fallback (event layer): the winner-team player with
    # the highest round impact (kills + 0.45 * assists).  The scoreboard layer
    # recomputes this from exact per-round deltas when available.
    round_mvps: dict[int, int] = {}
    for round_item in counted_rounds:
        winner_key = None
        if round_item.winner_side in SIDES:
            for key in ("A", "B"):
                if team_side.get((round_item.index, key)) == round_item.winner_side:
                    winner_key = key
                    break
        if winner_key is None:
            continue
        best: tuple[float, int] | None = None
        for player in players:
            if player.team_key != winner_key:
                continue
            impact = player.kill_rounds.get(round_item.index, 0) + 0.45 * (
                1 if round_item.index in player.assist_rounds else 0
            )
            if impact <= 0:
                continue
            if best is None or impact > best[0]:
                best = (impact, player.steamid)
        if best is not None:
            round_mvps[round_item.round_num] = best[1]
    for player in players:
        mvp_total = 0
        mvp_side = {SIDE_T: 0, SIDE_CT: 0}
        for round_item in counted_rounds:
            if round_mvps.get(round_item.round_num) != player.steamid:
                continue
            mvp_total += 1
            side_key = team_side.get((round_item.index, player.team_key))
            if side_key in SIDES:
                mvp_side[side_key] += 1
        player.raw.mvps = mvp_total

        for side in SIDES:
            counter = player.side[side]
            counter.mvps = mvp_side[side]
            counter.rounds = sum(
                1 for round_item in counted_rounds if team_side.get((round_item.index, player.team_key)) == side
            )
            counter.kast_rounds = 0
            counter.survived_rounds = 0
            counter.multikill_rounds = 0
            counter.multikill_2k = 0
            counter.multikill_3k = 0
            counter.multikill_4k = 0
            counter.multikill_5k = 0
            for round_item in counted_rounds:
                round_index = round_item.index
                if team_side.get((round_index, player.team_key)) != side:
                    continue
                if (
                    player.kill_rounds.get(round_index)
                    or round_index in player.assist_rounds
                    or round_index not in player.death_rounds
                    or round_index in player.traded_rounds
                ):
                    counter.kast_rounds += 1
                if round_index not in player.death_rounds:
                    counter.survived_rounds += 1
            for round_index, count in player.kill_rounds.items():
                if team_side.get((round_index, player.team_key)) != side:
                    continue
                if count == 2:
                    counter.multikill_2k += 1
                elif count == 3:
                    counter.multikill_3k += 1
                elif count == 4:
                    counter.multikill_4k += 1
                elif count >= 5:
                    counter.multikill_5k += 1
                if count >= 2:
                    counter.multikill_rounds += 1

    # The official statistics are the formal-round event rebuild.  awpy's
    # demo.stats remains available as a cross-check, but it can include
    # warm-up/knife/draw events that have no counted round.
    for player in players:
        player.exact = _exact_from_raw(player.raw, total_rounds)

    death_diagnostics: list[dict[str, Any]] = []
    for player in players:
        if player.raw.deaths <= total_rounds:
            continue
        detail = []
        for round_index, count in sorted(player.death_round_counts.items()):
            detail.append(
                {
                    "round_num": rounds[round_index].round_num,
                    "count": count,
                }
            )
        death_diagnostics.append(
            {
                "player": player.name,
                "steamid": player.steamid,
                "deaths": player.raw.deaths,
                "rounds": total_rounds,
                "rounds_detail": detail,
            }
        )

    internal_rows = _internal_check(players, total_rounds)

    side_warnings: list[str] = []
    for player in players:
        side_warnings.extend(_side_consistency(player))
    if check_internal and side_warnings:
        raise ReconstructionError(
            "内部一致性校验失败：选手的 All 统计与 T/CT 分面之和不一致。"
            "这通常意味着每回合阵营识别有误，T/CT 数据不可信。",
            [
                {"player": "", "field": "side_consistency", "source": "", "rebuilt": warning, "delta": ""}
                for warning in side_warnings
            ],
        )

    teams: dict[str, Team] = {
        "A": Team(
            key="A",
            name=team_names["A"],
            color="#98ffff",
            start_side=a_start_side,
            players=[player for player in players if player.team_key == "A"],
        ),
        "B": Team(
            key="B",
            name=team_names["B"],
            color="#ffff00",
            start_side=b_start_side,
            players=[player for player in players if player.team_key == "B"],
        ),
    }
    for round_item in counted_rounds:
        winner_side = round_item.winner_side
        if winner_side not in SIDES:
            continue
        for key, team in teams.items():
            if team_side.get((round_item.index, key)) == winner_side:
                team.score += 1
    for team in teams.values():
        team.total_kills = sum(player.exact_int("kills") for player in team.players)

    map_name = str(header.get("map_name") or "").strip()
    if not map_name:
        map_name = demo_path.stem
    if map_name.startswith("de_"):
        map_name = map_name[3:]

    parsed = ParsedDemo(
        path=demo_path,
        sha256=digest,
        map_name=map_name,
        tickrate=tickrate,
        header=header,
        rounds=rounds,
        counted_rounds=counted_rounds,
        players=players,
        teams=teams,
        team_of=team_of,
        team_side=team_side,
        economy=economy,
        matrix=matrix,
        internal_rows=internal_rows,
        side_warnings=side_warnings + team_warnings,
        stats_differences=list(internal_rows),
        dropped_rounds=dropped_rounds,
        death_diagnostics=death_diagnostics,
        kill_feed=kill_feed,
        round_mvps=round_mvps,
    )
    try:
        from .scoreboard import apply_scoreboard
        apply_scoreboard(parsed, str(demo_path))
    except Exception as exc:
        parsed.side_warnings.append(f"计分板属性层未应用（{exc}）；精确层保持事件重建。")
    try:
        from .extras import apply_extras
        apply_extras(parsed)
    except Exception as exc:
        parsed.side_warnings.append(f"扩展数据层未应用（{exc}）。")
    if recording_warnings:
        parsed.consistency_warnings.extend(recording_warnings)
        parsed.data_quality["recording_truncation"] = {
            "unended_rounds": [
                item.round_num for item in parsed.rounds if not item.is_formal and item.reason_name == "still_in_progress"
            ],
            "message": recording_warnings[0],
        }
    store(parsed)
    return parsed
