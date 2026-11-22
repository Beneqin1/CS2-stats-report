"""Live CS2 Game State Integration (GSI) collector and report adapter.

This module is deliberately honest about what GSI can and cannot provide:

* ``player.match_stats`` gives K/D/A, and (in spectator mode) the same for all
  ten players.  Those are the only numbers we treat as directly reported.
* ``state.round_totaldmg`` is a per-round damage counter.  In spectator mode it
  can be summed into an ADR-like value, but it has not been checked against
  demo's health-capped damage; the report labels it as a GSI estimate.
* GSI has no attacker/victim kill events.  Kill matrix and opening kills are
  inferred by diffing successive snapshots and are marked as inferred.
* Normal (non-spectator) play exposes only the local player.  The report then
  contains one player, not ten; it never invents opponents.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import SIDE_CT, SIDE_T, SIDES, TARGET_DEFAULT, TEAM_COLORS
from .demo_parser import Counters, Player, Round, Team
from .report_data import build_report
from .render import render_report
from .utils import safe_div

LOGGER = logging.getLogger(__name__)

TABLE_FIELDS = (
    "kills",
    "deaths",
    "assists",
    "adr",
    "kast",
    "opening_kills",
    "multikill_rounds",
    "headshot_kills",
    "flash_assists",
    "traded_deaths",
    "swing_pct",
    "rating",
)
ALL_TABLE_FIELDS = TABLE_FIELDS


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _normalize_side(value: Any) -> str | None:
    text = _as_str(value).strip().upper()
    if text in ("T", "TERRORIST", "TERRORISTS", "2"):
        return SIDE_T
    if text in ("CT", "COUNTERTERRORIST", "COUNTER-TERRORIST", "3"):
        return SIDE_CT
    return None


def _opposite(side: str | None) -> str | None:
    if side == SIDE_T:
        return SIDE_CT
    if side == SIDE_CT:
        return SIDE_T
    return None


@dataclass
class RoundPlayer:
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    damage: int = 0
    headshot_kills: int = 0
    max_round_kills: int = 0
    last_round_kills: int = 0
    health: int | None = None
    survived: bool = False
    kast: bool = False
    opening: bool = False
    damage_seen: bool = False


@dataclass
class LiveRound:
    index: int
    number: int
    team_side: dict[str, str] = field(default_factory=dict)
    winner_side: str | None = None
    stats: dict[int, RoundPlayer] = field(default_factory=dict)
    complete: bool = True
    opening_done: bool = False
    opening_ambiguous: bool = False
    opening_sid: int | None = None
    matrix_ambiguous: bool = False
    started_at: float = 0.0
    ended_at: float | None = None


@dataclass
class LivePlayer:
    steamid: int
    name: str
    team_key: str
    first_seen: float
    last_seen: float
    side: str | None = None
    totals: dict[str, int] = field(default_factory=lambda: {"kills": 0, "deaths": 0, "assists": 0})
    state: dict[str, Any] = field(default_factory=dict)
    damage_seen: bool = False
    kast_seen: bool = False
    opening_seen: bool = False
    seen_allplayers: bool = False


class LiveData:
    """A ParsedDemo-compatible view used by ``report_data.build_report``."""

    def __init__(
        self,
        *,
        path: Path,
        map_name: str,
        rounds: list[Round],
        players: list[Player],
        teams: dict[str, Team],
        matrix: dict[str, dict[str, dict[tuple[int, int], int]]],
        team_of: dict[int, str],
        team_side: dict[tuple[int, str], str],
        field_availability: dict[int, dict[str, list[str]]],
        meta: dict[str, Any],
    ) -> None:
        self.path = path
        self.sha256 = "live-gsi"
        self.map_name = map_name
        self.tickrate = 64.0
        self.header = {"map_name": f"de_{map_name}" if map_name else ""}
        self.rounds = rounds
        self.counted_rounds = rounds
        self.players = players
        self.teams = teams
        self.team_of = team_of
        self.team_side = team_side
        self.economy: dict[tuple[int, str], int] = {}
        self.matrix = matrix
        self.internal_rows: list[dict[str, Any]] = []
        self.side_warnings: list[str] = []
        self.field_availability = field_availability
        self.supports_awp_matrix = False
        self.meta = meta

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


class LiveMatch:
    """Accumulate a single live match from GSI JSON payloads."""

    def __init__(self) -> None:
        self.map_name = ""
        self.map_phase = ""
        self.provider_steamid: int | None = None
        self.local_steamid: int | None = None
        self.source = "unknown"
        self.players: dict[int, LivePlayer] = {}
        self.player_team: dict[int, str] = {}
        self.team_start_side: dict[str, str] = {}
        self.team_by_side: dict[str, str] = {}
        self.scores: dict[str, int] = {"A": 0, "B": 0}
        self.rounds: list[LiveRound] = []
        self.active: LiveRound | None = None
        self.baseline: dict[int, tuple[int, int, int]] = {}
        self.last_snapshot: dict[int, tuple[int, int, int]] = {}
        self.matrix: dict[str, dict[str, Counter[tuple[int, int]]]] = {
            scope: {side: Counter() for side in SIDES} for scope in ("all", "first", "awp")
        }
        self.incomplete: list[str] = []
        self.finished = False
        self.finished_at: float | None = None
        self.last_payload_at: float | None = None
        self.last_payload: dict[str, Any] | None = None
        self.last_pre_gameover: dict[str, Any] | None = None
        self.final_totals: dict[int, dict[str, int]] = {}
        self.final_states: dict[int, dict[str, Any]] = {}
        self.menu_seen = False
        self.match_id = ""

    # ------------------------------------------------------------------
    # payload ingestion
    # ------------------------------------------------------------------
    def feed(self, payload: dict[str, Any], received_at: float | None = None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        now = time.monotonic() if received_at is None else float(received_at)
        events: list[str] = []
        self.last_payload = payload
        self.last_payload_at = now

        map_info = payload.get("map") or {}
        round_info = payload.get("round") or {}
        provider = payload.get("provider") or {}
        player_info = payload.get("player") or {}
        allplayers = payload.get("allplayers") or {}

        if provider.get("steamid"):
            try:
                self.provider_steamid = int(str(provider["steamid"]))
                if self.local_steamid is None:
                    self.local_steamid = self.provider_steamid
            except (TypeError, ValueError):
                pass

        if not map_info:
            if _as_str(player_info.get("activity")) == "menu":
                self.menu_seen = True
            return events

        map_name = _as_str(map_info.get("name"))
        if map_name:
            map_name = map_name.removeprefix("de_")
        if map_name and not self.map_name:
            self.map_name = map_name
            self.match_id = f"{map_name}-{int(time.time())}"
        elif map_name and self.map_name and map_name != self.map_name:
            # A different map means a different match.  The caller should have
            # produced the previous report already; reset quietly for the next.
            self._reset(map_name)

        self.map_phase = _as_str(map_info.get("phase")).lower()
        round_phase = _as_str(round_info.get("phase")).lower()

        if allplayers:
            self.source = "spectator"
            self.incomplete = [
                note for note in self.incomplete if "未进入观察者模式" not in note
            ]
        elif player_info.get("steamid") and self.source == "unknown":
            self.source = "local"

        if allplayers:
            self._observe(allplayers, now, from_allplayers=True)
        elif player_info.get("steamid"):
            sid = _as_int(player_info.get("steamid"))
            self._observe({str(sid): player_info}, now)

        round_number = _as_int(map_info.get("round"), 0) + 1
        if self.map_phase == "live" and round_phase in ("freezetime", "live", "over"):
            if self.active is None:
                self._start_round(round_number, now)
            elif self.active.number != round_number:
                self._finalize_round(now)
                self._start_round(round_number, now)

        if self.active is not None:
            self._update_round(payload, now)

        if round_phase == "over" or self.map_phase == "intermission":
            self._finalize_round(now)

        if self.map_phase == "gameover":
            self._finalize_round(now)
            if not self.finished:
                self.finished = True
                self.finished_at = now
                self._capture_final()
                events.append("match_end")

        if self.map_phase not in ("gameover", ""):
            self.last_pre_gameover = payload

        return events

    def _reset(self, map_name: str) -> None:
        self.__init__()
        self.map_name = map_name
        self.match_id = f"{map_name}-{int(time.time())}"

    # ------------------------------------------------------------------
    # roster / team identity
    # ------------------------------------------------------------------
    def _observe(
        self,
        roster: dict[str, Any],
        now: float,
        *,
        from_allplayers: bool = False,
    ) -> None:
        sides: dict[int, str | None] = {}
        for raw_sid, raw in roster.items():
            if not isinstance(raw, dict):
                continue
            sid = _as_int(raw_sid)
            if sid <= 0:
                continue
            name = _as_str(raw.get("name")) or self.players.get(sid, LivePlayer(sid, str(sid), "A", now, now)).name
            state = raw.get("state") or {}
            side = _normalize_side(raw.get("team")) or _normalize_side(state.get("team"))
            sides[sid] = side
            match_stats = raw.get("match_stats") or {}
            totals = {
                "kills": _as_int(match_stats.get("kills")),
                "deaths": _as_int(match_stats.get("deaths")),
                "assists": _as_int(match_stats.get("assists")),
            }
            existing = self.players.get(sid)
            if existing is None:
                existing = LivePlayer(
                    steamid=sid,
                    name=name,
                    team_key="",
                    first_seen=now,
                    last_seen=now,
                    side=side,
                    totals=totals,
                    state=dict(state),
                    seen_allplayers=from_allplayers,
                )
                self.players[sid] = existing
            else:
                existing.name = name
                existing.last_seen = now
                existing.side = side or existing.side
                existing.totals = totals
                existing.state = dict(state)
                existing.seen_allplayers = existing.seen_allplayers or from_allplayers
            if state.get("round_totaldmg") is not None:
                existing.damage_seen = True

        self._assign_teams(sides)
        self._update_team_by_side()

        for sid in sides:
            player = self.players.get(sid)
            if player is None or not player.team_key:
                continue
            if self.active is not None and sid not in self.active.stats:
                self.active.stats[sid] = RoundPlayer()
                if self.active.index > 0:
                    self.active.complete = False
                    note = "中途加入的选手缺少回合开始快照"
                    if note not in self.incomplete:
                        self.incomplete.append(note)
                    self.baseline[sid] = self._totals(sid)
                else:
                    self.baseline[sid] = (0, 0, 0)
                self.last_snapshot[sid] = (
                    (0, 0, 0) if self.active.index == 0 else self._totals(sid)
                )

    def _assign_teams(self, sides: dict[int, str | None]) -> None:
        if not sides:
            return
        if not self.player_team:
            by_side: dict[str, list[int]] = defaultdict(list)
            for sid, side in sides.items():
                if side:
                    by_side[side].append(sid)
            a_side: str | None = None
            if self.local_steamid is not None and self.local_steamid in sides:
                a_side = sides[self.local_steamid]
            if a_side is None and SIDE_CT in by_side:
                a_side = SIDE_CT
            if a_side is None and by_side:
                a_side = next(iter(by_side))
            for sid, side in sides.items():
                if not side:
                    continue
                self.player_team[sid] = "A" if side == a_side else "B"
            if a_side:
                self.team_start_side["A"] = a_side
                self.team_start_side["B"] = _opposite(a_side) or ""
            return

        # Players already known keep their identity.  New players are assigned
        # to the team currently on their side, falling back to the smaller team.
        for sid, side in sides.items():
            if sid in self.player_team or not side:
                continue
            candidates = [
                key
                for key in ("A", "B")
                if self.team_by_side.get(key) == side
                or any(
                    p.team_key == key and p.side == side
                    for p in self.players.values()
                    if p.steamid != sid
                )
            ]
            if not candidates:
                counts = Counter(self.player_team.values())
                candidates = [min(("A", "B"), key=lambda k: counts.get(k, 0))]
            self.player_team[sid] = candidates[0]

        for sid, player in self.players.items():
            if sid in self.player_team:
                player.team_key = self.player_team[sid]
                if player.team_key not in self.team_start_side and player.side:
                    self.team_start_side[player.team_key] = player.side

    def _update_team_by_side(self) -> None:
        for key in ("A", "B"):
            votes = Counter(
                p.side
                for p in self.players.values()
                if p.team_key == key and p.side in SIDES
            )
            if votes:
                self.team_by_side[key] = votes.most_common(1)[0][0]
        if "A" in self.team_by_side and "B" not in self.team_by_side:
            side = _opposite(self.team_by_side["A"])
            if side:
                self.team_by_side["B"] = side
        if "B" in self.team_by_side and "A" not in self.team_by_side:
            side = _opposite(self.team_by_side["B"])
            if side:
                self.team_by_side["A"] = side

    def _current_team_side(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in ("A", "B"):
            votes = Counter(
                p.side
                for p in self.players.values()
                if p.team_key == key and p.side in SIDES
            )
            if votes:
                result[key] = votes.most_common(1)[0][0]
            elif self.team_start_side.get(key) in SIDES:
                result[key] = self.team_start_side[key]
        if "A" in result and "B" not in result:
            other = _opposite(result["A"])
            if other:
                result["B"] = other
        if "B" in result and "A" not in result:
            other = _opposite(result["B"])
            if other:
                result["A"] = other
        return result

    # ------------------------------------------------------------------
    # round handling
    # ------------------------------------------------------------------
    def _totals(self, sid: int) -> tuple[int, int, int]:
        player = self.players.get(sid)
        if player is None:
            return (0, 0, 0)
        return (
            player.totals.get("kills", 0),
            player.totals.get("deaths", 0),
            player.totals.get("assists", 0),
        )

    def _start_round(self, number: int, now: float) -> None:
        index = len(self.rounds)
        self.active = LiveRound(
            index=index,
            number=number,
            team_side=self._current_team_side(),
            stats={sid: RoundPlayer() for sid in self.players},
            started_at=now,
        )
        # In the first round the match totals start at zero.  For later rounds
        # the start-of-round totals are the previous end-of-round totals.
        self.baseline = {
            sid: ((0, 0, 0) if index == 0 else self._totals(sid))
            for sid in self.players
        }
        self.last_snapshot = {sid: self._totals(sid) for sid in self.players}
        if self.source == "local" and "未进入观察者模式：只有本地玩家" not in self.incomplete:
            self.incomplete.append("未进入观察者模式：只有本地玩家")

    def _update_round(self, payload: dict[str, Any], now: float) -> None:
        if self.active is None:
            return
        roster = payload.get("allplayers") or {}
        if not roster:
            player_info = payload.get("player") or {}
            sid = _as_int(player_info.get("steamid"))
            if sid > 0:
                roster = {str(sid): player_info}

        current_sides = self._current_team_side()
        if current_sides:
            self.active.team_side = current_sides

        # Opening kill: compare round_kills counters across this payload.
        increases: dict[int, int] = {}
        for raw_sid, raw in roster.items():
            sid = _as_int(raw_sid)
            rp = self.active.stats.get(sid)
            if rp is None or not isinstance(raw, dict):
                continue
            state = raw.get("state") or {}
            rk = _as_int(state.get("round_kills"))
            if rk > rp.last_round_kills:
                increases[sid] = rk - rp.last_round_kills
        if increases and not self.active.opening_done:
            if len(increases) == 1:
                sid = next(iter(increases))
                rp = self.active.stats.get(sid)
                if rp is not None:
                    rp.opening = True
                    self.active.opening_sid = sid
            else:
                self.active.opening_ambiguous = True
            self.active.opening_done = True

        prev_totals = {
            sid: self.last_snapshot.get(sid, (0, 0, 0)) for sid in self.players
        }
        for raw_sid, raw in roster.items():
            sid = _as_int(raw_sid)
            if sid <= 0 or not isinstance(raw, dict):
                continue
            player = self.players.get(sid)
            if player is None:
                continue
            rp = self.active.stats.get(sid)
            if rp is None:
                rp = RoundPlayer()
                self.active.stats[sid] = rp

            state = raw.get("state") or {}
            match_stats = raw.get("match_stats") or {}
            if match_stats:
                player.totals = {
                    "kills": _as_int(match_stats.get("kills")),
                    "deaths": _as_int(match_stats.get("deaths")),
                    "assists": _as_int(match_stats.get("assists")),
                }
            if state:
                player.state = dict(state)
                if state.get("round_totaldmg") is not None:
                    player.damage_seen = True
                    rp.damage_seen = True
                    rp.damage = max(rp.damage, _as_int(state.get("round_totaldmg")))
                rp.headshot_kills = max(rp.headshot_kills, _as_int(state.get("round_killhs")))
                rk = _as_int(state.get("round_kills"))
                rp.max_round_kills = max(rp.max_round_kills, rk)
                rp.last_round_kills = rk
                health = state.get("health")
                if health is not None:
                    rp.health = _as_int(health)
                    rp.survived = rp.health > 0

            base = self.baseline.get(sid, (0, 0, 0))
            kills, deaths, assists = self._totals(sid)
            rp.kills = max(0, kills - base[0])
            rp.deaths = max(0, deaths - base[1])
            rp.assists = max(0, assists - base[2])

        # Pair one kill increase with one death increase.  Anything with more
        # than one kill/death in the same snapshot is ambiguous and is skipped.
        current = {sid: self._totals(sid) for sid in self.players}
        deltas = {
            sid: (
                current[sid][0] - prev_totals.get(sid, (0, 0, 0))[0],
                current[sid][1] - prev_totals.get(sid, (0, 0, 0))[1],
            )
            for sid in current
        }
        self._infer_matrix_from_deltas(deltas, current_sides)
        self.last_snapshot = current

    def _infer_matrix_from_deltas(
        self,
        deltas: dict[int, tuple[int, int]],
        current_sides: dict[str, str],
    ) -> None:
        if self.active is None:
            return
        if any(dk > 1 or dd > 1 for dk, dd in deltas.values()):
            self.active.matrix_ambiguous = True
            self.active.complete = False
        killers = [sid for sid, (dk, _dd) in deltas.items() if dk == 1]
        victims = [sid for sid, (_dk, dd) in deltas.items() if dd == 1]
        if len(killers) != 1 or len(victims) != 1:
            return
        killer, victim = killers[0], victims[0]
        if killer == victim:
            return
        side = current_sides.get(self.player_team.get(killer, ""), None)
        if side not in SIDES:
            return
        self.matrix["all"][side][(killer, victim)] += 1
        if self.active.stats.get(killer) and self.active.stats[killer].opening:
            self.matrix["first"][side][(killer, victim)] += 1

    def _finalize_round(self, now: float) -> None:
        if self.active is None:
            return
        active = self.active
        active.ended_at = now
        active.team_side = self._current_team_side() or active.team_side
        for rp in active.stats.values():
            rp.kast = bool(rp.kills > 0 or rp.assists > 0 or rp.survived)
            if rp.health is not None:
                rp.survived = rp.health > 0
        if active.winner_side is None and self.last_payload:
            winner = _normalize_side((self.last_payload.get("round") or {}).get("win_team"))
            if winner:
                active.winner_side = winner
        self.rounds.append(active)
        if active.winner_side:
            for key, side in active.team_side.items():
                if side == active.winner_side:
                    self.scores[key] = self.scores.get(key, 0) + 1
        if active.opening_ambiguous:
            note = "同一快照内出现多个击杀，首杀归属不确定"
            if note not in self.incomplete:
                self.incomplete.append(note)
        if active.matrix_ambiguous:
            note = "同一快照内出现多杀，击杀配对可能缺失"
            if note not in self.incomplete:
                self.incomplete.append(note)
        self.active = None

    def _capture_final(self) -> None:
        self.final_totals = {sid: dict(p.totals) for sid, p in self.players.items()}
        self.final_states = {sid: dict(p.state) for sid, p in self.players.items()}

    # ------------------------------------------------------------------
    # report data
    # ------------------------------------------------------------------
    def freeze(self) -> LiveData:
        if not self.rounds:
            raise ValueError("还没有完整的回合数据")
        rounds = [
            Round(
                index=r.index,
                round_num=r.number,
                start_tick=None,
                freeze_end_tick=None,
                end_tick=None,
                official_end_tick=None,
                winner_side=r.winner_side,
                is_knife=False,
            )
            for r in self.rounds
        ]
        players: list[Player] = []
        team_names = {"A": "Team A", "B": "Team B"}
        teams: dict[str, Team] = {
            key: Team(
                key=key,
                name=team_names[key],
                color=TEAM_COLORS.get(key, "#98ffff"),
                start_side=self.team_start_side.get(key, ""),
            )
            for key in ("A", "B")
        }
        team_of: dict[int, str] = {}
        field_availability: dict[int, dict[str, list[str]]] = {}

        eligible = self.players
        if self.source == "spectator":
            eligible = {
                sid: live
                for sid, live in self.players.items()
                if live.seen_allplayers or any(live.totals.values())
            }

        global_damage = any(live.damage_seen for live in eligible.values())
        global_spectator = self.source == "spectator"

        for sid, live in sorted(eligible.items(), key=lambda item: item[1].name.casefold()):
            team_key = self.player_team.get(sid) or live.team_key or "A"
            team = teams.get(team_key)
            if team is None:
                team = teams[team_key] = Team(
                    key=team_key,
                    name=team_names.get(team_key, team_key),
                    color=TEAM_COLORS.get(team_key, "#98ffff"),
                    start_side=self.team_start_side.get(team_key, ""),
                )
            team_of[sid] = team_key
            rounds_played = len(rounds)
            totals = self.final_totals.get(sid) or dict(live.totals)
            raw = Counters(rounds=rounds_played)
            raw.kills = _as_int(totals.get("kills"))
            raw.deaths = _as_int(totals.get("deaths"))
            raw.assists = _as_int(totals.get("assists"))
            raw.kill_weight = float(raw.kills)
            raw.death_weight = float(raw.deaths)
            side = {side_key: Counters(rounds=0) for side_key in SIDES}
            damage_total = 0
            damage_seen = False
            kast_seen = False
            opening_seen = False
            hs_seen = False
            mk_seen = False
            for r in self.rounds:
                rp = r.stats.get(sid)
                if rp is None:
                    continue
                team_side = r.team_side.get(team_key)
                if team_side not in SIDES:
                    continue
                counter = side[team_side]
                counter.rounds += 1
                counter.kills += rp.kills
                counter.deaths += rp.deaths
                counter.assists += rp.assists
                counter.damage += rp.damage
                counter.headshot_kills += rp.headshot_kills
                counter.kast_rounds += int(bool(rp.kast))
                counter.survived_rounds += int(bool(rp.survived))
                counter.opening_kills += int(bool(rp.opening))
                if rp.max_round_kills >= 2:
                    counter.multikill_rounds += 1
                    if rp.max_round_kills == 2:
                        counter.multikill_2k += 1
                    elif rp.max_round_kills == 3:
                        counter.multikill_3k += 1
                    elif rp.max_round_kills == 4:
                        counter.multikill_4k += 1
                    else:
                        counter.multikill_5k += 1
                counter.kill_weight = float(counter.kills)
                counter.death_weight = float(counter.deaths)
                damage_total += rp.damage
                damage_seen = damage_seen or rp.damage_seen
                kast_seen = kast_seen or (self.source == "spectator")
                opening_seen = opening_seen or bool(rp.opening)
                hs_seen = hs_seen or bool(rp.headshot_kills)
                mk_seen = mk_seen or bool(rp.max_round_kills)
            raw.damage = damage_total
            raw.kast_rounds = sum(c.kast_rounds for c in side.values())
            raw.survived_rounds = sum(c.survived_rounds for c in side.values())
            raw.opening_kills = sum(c.opening_kills for c in side.values())
            raw.headshot_kills = sum(c.headshot_kills for c in side.values())
            raw.multikill_rounds = sum(c.multikill_rounds for c in side.values())
            for attr in ("multikill_2k", "multikill_3k", "multikill_4k", "multikill_5k"):
                setattr(raw, attr, sum(getattr(c, attr) for c in side.values()))
            raw.utility_damage = 0
            exact = {
                "kills": raw.kills,
                "deaths": raw.deaths,
                "assists": raw.assists,
                "adr": raw.adr if damage_seen else 0.0,
                "kast": raw.kast if kast_seen else 0.0,
                "opening_kills": raw.opening_kills if opening_seen else 0,
            }
            availability: dict[str, list[str]] = {}
            base_available = [
                "kills", "deaths", "assists", "rating", "impact",
                "kpr", "dpr", "apr", "survival", "kdr", "plus_minus", "kd",
            ]
            for side_key in ("ALL", *SIDES):
                available = list(base_available)
                if global_damage or damage_seen:
                    available.append("adr")
                if global_spectator or kast_seen:
                    available.append("kast")
                if global_spectator or opening_seen:
                    available.append("opening_kills")
                if global_spectator or mk_seen:
                    available.append("multikill_rounds")
                if global_spectator or hs_seen:
                    available.append("headshot_kills")
                availability[side_key] = available
            player = Player(
                steamid=sid,
                name=live.name,
                team_key=team_key,
                exact=exact,
                rounds_played=rounds_played,
                raw=raw,
                side=side,
            )
            players.append(player)
            field_availability[sid] = availability
            team.players.append(player)
            team.total_kills += raw.kills

        for team in teams.values():
            team.score = self.scores.get(team.key, 0)
        matrix: dict[str, dict[str, dict[tuple[int, int], int]]] = {}
        for scope, by_side in self.matrix.items():
            matrix[scope] = {side: dict(counter) for side, counter in by_side.items()}

        meta = {
            "mode": "live",
            "source": "spectator" if self.source == "spectator" else "local",
            "incomplete": list(dict.fromkeys(self.incomplete)),
            "local_steamid": self.local_steamid,
            "spectator": self.source == "spectator",
            "live_started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return LiveData(
            path=Path(f"live_{self.map_name or 'unknown'}.dem"),
            map_name=self.map_name,
            rounds=rounds,
            players=players,
            teams=teams,
            matrix=matrix,
            team_of=team_of,
            team_side={(r.index, key): value for r in self.rounds for key, value in r.team_side.items()},
            field_availability=field_availability,
            meta=meta,
        )


# ----------------------------------------------------------------------
# live validation payload
# ----------------------------------------------------------------------
def live_validation(match: LiveMatch) -> dict[str, Any]:
    source = "spectator（10 人）" if match.source == "spectator" else "本地玩家（未进入观察者模式）"
    incomplete = list(dict.fromkeys(match.incomplete))
    notes = [
        "Kills / Deaths / Assists 来自 GSI player.match_stats，未与计分板 fixture 逐项校验。",
        "ADR 使用 spectator 每回合 round_totaldmg 求和；与 demo 的生命值封顶口径未对齐，仅作参考。",
        "KAST 为 K/A/S 推断（缺少 traded 判定）；Open Kills 为回合首个击杀增量推断；"
        "Kill Matrix 为相邻快照差分推断，并发击杀可能缺失。",
        "Rating / Swing 仍为近似实现，不是 HLTV 官方数值。",
    ]
    if match.source != "spectator":
        notes.insert(0, "没有 allplayers：本报告只有本地玩家，不能生成其余 9 人的数据。")
    if incomplete:
        notes.append("数据缺口：" + "；".join(incomplete))
    return {
        "status": "live",
        "title": "GSI 实时数据（未与计分板 fixture 校验）",
        "message": "来源：" + source + "。" + " ".join(notes),
        "source": "spectator" if match.source == "spectator" else "local",
        "incomplete": incomplete,
        "fixture": None,
        "checks": [],
    }


def build_live_report(match: LiveMatch, target_name: str = TARGET_DEFAULT) -> dict[str, Any]:
    data = match.freeze()
    report = build_report(data, target_name=target_name, validation=live_validation(match))
    report["meta"].update(data.meta)
    report["meta"]["demo_file"] = (
        "GSI 实时监听 · spectator（10 人）"
        if match.source == "spectator"
        else "GSI 实时监听 · 本地玩家"
    )
    report["meta"]["demo_sha256"] = "live-gsi"
    report["meta"]["live_incomplete"] = data.meta["incomplete"]
    return report


def render_live_report(match: LiveMatch, output_path: str | Path, target_name: str = TARGET_DEFAULT) -> Path:
    report = build_live_report(match, target_name)
    return render_report(report, Path(output_path))


# ----------------------------------------------------------------------
# HTTP listener
# ----------------------------------------------------------------------
class _GSIHandler(BaseHTTPRequestHandler):
    server: "_GSIServer"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOGGER.debug("GSI %s", fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"CS2 Stats GSI listener")

    def do_POST(self) -> None:  # noqa: N802
        length = _as_int(self.headers.get("Content-Length"), 0)
        body = self.rfile.read(length) if length > 0 else b""
        token = self.server.token
        if token:
            supplied = self.headers.get("X-Auth-Token") or self.headers.get("Authorization") or ""
            if supplied != token and supplied != f"Bearer {token}":
                self.send_response(403)
                self.end_headers()
                return
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            LOGGER.exception("invalid GSI JSON")
            self.send_response(400)
            self.end_headers()
            return
        try:
            events = self.server.match.feed(payload)
            self.server.on_events(events)
        except Exception:
            LOGGER.exception("GSI payload processing failed")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _GSIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        match: LiveMatch,
        token: str,
        on_events: Callable[[list[str]], None],
    ) -> None:
        self.match = match
        self.token = token
        self.on_events = on_events
        super().__init__(address, _GSIHandler)


class GSIListener:
    """Run a local GSI HTTP endpoint in a background thread."""

    def __init__(
        self,
        port: int = 32123,
        token: str = "cs2stats-local",
        on_events: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.port = port
        self.token = token
        self.match = LiveMatch()
        self.on_events = on_events or (lambda events: None)
        self._server: _GSIServer | None = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> None:
        try:
            self._server = _GSIServer(
                ("127.0.0.1", self.port), self.match, self.token, self.on_events
            )
        except OSError as exc:
            self.error = f"无法监听 127.0.0.1:{self.port}：{exc}"
            raise
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="gsi-listener")
        self._thread.start()
        LOGGER.info("GSI listener on 127.0.0.1:%s", self.port)

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                LOGGER.debug("GSI shutdown failed", exc_info=True)
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    @property
    def running(self) -> bool:
        return self._server is not None
