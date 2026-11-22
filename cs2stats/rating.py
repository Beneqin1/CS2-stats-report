"""Approximate performance metrics.

The exact layer (kills/deaths/assists/ADR/KAST) is rebuilt from formal-round
events and checked against a scoreboard fixture elsewhere.  Everything in this
module is a deliberately transparent approximation, not an HLTV implementation:

* base rating: a Rating 2.1-style formula, with a lower Swing weight and a
  higher kill weight than the earlier build;
* economy delta: small bonus/penalty from equipment-weighted kills/deaths;
* swing: a man-advantage proxy, displayed as a percentage;
* MK rating: base rating plus a reduced multi-kill round bonus.

The report must label all of these as approximate.  Never feed them back into
the exact layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .config import (
    ECON_DEATH_RATING_COEF,
    ECON_RATING_COEF,
    ECON_RATING_LIMIT,
    IMPACT_APR_COEF,
    IMPACT_INTERCEPT,
    IMPACT_KPR_COEF,
    MK_RATING_BONUS,
    RATING_ADR_COEF,
    RATING_DPR_COEF,
    RATING_IMPACT_COEF,
    RATING_INTERCEPT,
    RATING_KAST_COEF,
    RATING_KPR_COEF,
    SWING_PER_ROUND,
    SWING_PCT_LIMIT,
    SWING_RATING_COEF,
)
from .demo_parser import Counters, ParsedDemo, Player
from .utils import clamp, safe_div


@dataclass(frozen=True)
class Metrics:
    rounds: int
    kills: int
    deaths: int
    assists: int
    flash_assists: int
    headshot_kills: int
    opening_kills: int
    opening_deaths: int
    traded_deaths: int
    multikill_2k: int
    multikill_3k: int
    multikill_4k: int
    multikill_5k: int
    multikill_rounds: int
    damage: int
    utility_damage: int
    kast_rounds: int
    survived_rounds: int
    kpr: float
    dpr: float
    apr: float
    kast: float
    adr: float
    survival: float
    impact: float
    base_rating: float
    econ_delta: float
    swing_pct: float
    rating: float
    mk_rating: float

    @property
    def available(self) -> bool:
        return self.rounds > 0

    @property
    def plus_minus(self) -> int:
        return self.kills - self.deaths

    @property
    def kd(self) -> float:
        if self.deaths <= 0:
            return float(self.kills) if self.kills else 0.0
        return self.kills / self.deaths


def metrics_from_counters(counter: Counters) -> Metrics:
    """Compute the approximate layer for one slice (All, T or CT)."""

    rounds = counter.rounds
    kpr = safe_div(counter.kills, rounds)
    dpr = safe_div(counter.deaths, rounds)
    apr = safe_div(counter.assists, rounds)
    kast = counter.kast
    adr = counter.adr
    survival = safe_div(counter.survived_rounds, rounds) * 100.0

    impact = IMPACT_KPR_COEF * kpr + IMPACT_APR_COEF * apr + IMPACT_INTERCEPT
    base = (
        RATING_KAST_COEF * kast
        + RATING_KPR_COEF * kpr
        + RATING_DPR_COEF * dpr
        + RATING_IMPACT_COEF * impact
        + RATING_ADR_COEF * adr
        + RATING_INTERCEPT
    )

    adjusted_kpr = safe_div(counter.kill_weight, rounds)
    adjusted_dpr = safe_div(counter.death_weight, rounds)
    econ_delta = clamp(
        ECON_RATING_COEF * (adjusted_kpr - kpr)
        + ECON_DEATH_RATING_COEF * (dpr - adjusted_dpr),
        -ECON_RATING_LIMIT,
        ECON_RATING_LIMIT,
    )

    swing_pct = clamp(
        100.0 * safe_div(counter.swing_sum, rounds * SWING_PER_ROUND),
        -SWING_PCT_LIMIT,
        SWING_PCT_LIMIT,
    )
    rating = max(0.0, base + econ_delta + SWING_RATING_COEF * swing_pct / 100.0)
    mk_rating = rating + MK_RATING_BONUS * safe_div(counter.multikill_rounds, rounds)

    return Metrics(
        rounds=rounds,
        kills=counter.kills,
        deaths=counter.deaths,
        assists=counter.assists,
        flash_assists=counter.flash_assists,
        headshot_kills=counter.headshot_kills,
        opening_kills=counter.opening_kills,
        opening_deaths=counter.opening_deaths,
        traded_deaths=counter.traded_deaths,
        multikill_2k=counter.multikill_2k,
        multikill_3k=counter.multikill_3k,
        multikill_4k=counter.multikill_4k,
        multikill_5k=counter.multikill_5k,
        multikill_rounds=counter.multikill_rounds,
        damage=counter.damage,
        utility_damage=counter.utility_damage,
        kast_rounds=counter.kast_rounds,
        survived_rounds=counter.survived_rounds,
        kpr=kpr,
        dpr=dpr,
        apr=apr,
        kast=kast,
        adr=adr,
        survival=survival,
        impact=impact,
        base_rating=base,
        econ_delta=econ_delta,
        swing_pct=swing_pct,
        rating=rating,
        mk_rating=mk_rating,
    )


@dataclass
class PlayerMetrics:
    player: Player
    all: Metrics
    sides: dict[str, Metrics] = field(default_factory=dict)

    def for_side(self, side: str) -> Metrics:
        if side == "ALL":
            return self.all
        return self.sides.get(side) or metrics_from_counters(Counters())


def build_player_metrics(demo: ParsedDemo) -> list[PlayerMetrics]:
    result: list[PlayerMetrics] = []
    for player in demo.players:
        result.append(
            PlayerMetrics(
                player=player,
                all=metrics_from_counters(player.raw),
                sides={side: metrics_from_counters(player.side[side]) for side in ("T", "CT")},
            )
        )
    return result


_AVERAGE_FIELDS = (
    "kpr",
    "dpr",
    "apr",
    "kast",
    "adr",
    "survival",
    "impact",
    "rating",
    "mk_rating",
    "swing_pct",
    "econ_delta",
)


def average_metrics(items: Iterable[Metrics]) -> dict[str, float]:
    usable = [item for item in items if item.available]
    if not usable:
        return {name: 0.0 for name in _AVERAGE_FIELDS}
    return {
        name: sum(getattr(item, name) for item in usable) / len(usable)
        for name in _AVERAGE_FIELDS
    }


def average_rating(items: Iterable[Metrics]) -> float:
    values = [item.rating for item in items if item.available]
    return sum(values) / len(values) if values else 0.0


def weighted_team_average(items: Iterable[Metrics]) -> float:
    """Average rating weighted by rounds, useful for a small summary block."""

    usable = [item for item in items if item.available]
    total_rounds = sum(item.rounds for item in usable)
    if not usable or total_rounds <= 0:
        return 0.0
    return sum(item.rating * item.rounds for item in usable) / total_rounds
