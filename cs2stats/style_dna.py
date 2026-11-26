"""Style DNA: six-axis playstyle fingerprint and pro-similarity matching.

Pure data in / data out (no awpy imports) so the logic is unit-testable.
Every feature is computed only from rounds whose event attribution matched
the scoreboard (matrix_coverage); axes that lack coverage are reported as
unavailable instead of guessed.  Pro reference vectors are *preset profiles*
(hand-tuned from public playstyle knowledge), not parsed from pro demos —
they are documented as such in docs/style_dna.md.
"""

from __future__ import annotations

import math
from typing import Any

AXES = (
    ("aggression", "侵略"),
    ("survival", "存活"),
    ("late", "后段发力"),
    ("utility", "道具"),
    ("timing", "前期参与"),
    ("roam", "游走"),
)

# Preset pro profiles (0..1 per axis).  Not parsed from demos; starting
# points to be calibrated with real demo data later.  See docs/style_dna.md.
PRO_PROFILES: dict[str, dict[str, float]] = {
    "donk": {"aggression": 0.95, "survival": 0.45, "late": 0.30, "utility": 0.35, "timing": 0.85, "roam": 0.55},
    "ropz": {"aggression": 0.35, "survival": 0.85, "late": 0.85, "utility": 0.50, "timing": 0.35, "roam": 0.75},
    "ZywOo": {"aggression": 0.55, "survival": 0.80, "late": 0.60, "utility": 0.50, "timing": 0.50, "roam": 0.50},
    "karrigan": {"aggression": 0.60, "survival": 0.35, "late": 0.55, "utility": 0.90, "timing": 0.55, "roam": 0.80},
    "NiKo": {"aggression": 0.70, "survival": 0.55, "late": 0.45, "utility": 0.55, "timing": 0.65, "roam": 0.50},
    "m0NESY": {"aggression": 0.65, "survival": 0.70, "late": 0.55, "utility": 0.45, "timing": 0.60, "roam": 0.55},
    "s1mple (2020-2022)": {"aggression": 0.80, "survival": 0.60, "late": 0.55, "utility": 0.45, "timing": 0.65, "roam": 0.65},
    "twistzz": {"aggression": 0.55, "survival": 0.60, "late": 0.50, "utility": 0.60, "timing": 0.55, "roam": 0.50},
}

# Archetype prototypes (0..1 per axis).
ARCHETYPES: dict[str, dict[str, float]] = {
    "突破手": {"aggression": 0.90, "survival": 0.35, "late": 0.25, "utility": 0.40, "timing": 0.85, "roam": 0.50},
    "自由人": {"aggression": 0.35, "survival": 0.80, "late": 0.85, "utility": 0.45, "timing": 0.35, "roam": 0.75},
    "辅助": {"aggression": 0.45, "survival": 0.55, "late": 0.45, "utility": 0.90, "timing": 0.55, "roam": 0.55},
    "狙击核心": {"aggression": 0.55, "survival": 0.70, "late": 0.55, "utility": 0.40, "timing": 0.55, "roam": 0.45},
    "Anchor 防守核心": {"aggression": 0.30, "survival": 0.75, "late": 0.55, "utility": 0.65, "timing": 0.40, "roam": 0.20},
}

# Normalisation caps (documented in docs/style_dna.md).
_CAP_OPEN_RATE = 0.40       # opening-duel attempts per round
_CAP_DISTANCE_M = 25.0      # average engagement distance (metres)
_CAP_LATE_SHARE = 0.50      # share of kills in the last 25% of a round
_CAP_UTILITY_PER_ROUND = 1.5
_CAP_ROAM_M = 25.0          # attacker-position dispersion (metres)
_EARLY_SECONDS = 20.0       # "round opening" window for the timing axis
_LATE_FRACTION = 0.75       # last quarter of a round for the late axis
_AWP_SHARE = 0.35           # awp kill share that forces the AWPer archetype


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _reliable_round_indices(parsed: Any) -> set[int]:
    coverage = getattr(parsed, "matrix_coverage", {}) or {}
    excluded = set(coverage.get("excluded_rounds") or [])
    formal = [item for item in parsed.counted_rounds if item.is_formal]
    if not coverage:
        return {item.index for item in formal}
    return {item.index for item in formal if item.round_num not in excluded}


def compute_features(parsed: Any, steamid: int) -> dict[str, Any]:
    """Compute the six-axis feature vector for one player.

    Returns {"axes": {key: 0..1}, "raw": {...}, "coverage": {...}}.  Axes
    whose inputs are unavailable are None and excluded from matching.
    """

    reliable = _reliable_round_indices(parsed)
    player = next((p for p in parsed.players if int(p.steamid) == int(steamid)), None)
    if player is None:
        return {"axes": {}, "raw": {}, "coverage": {"reliable_rounds": 0}, "available": False}

    tickrate = float(getattr(parsed, "tickrate", 0.0)) or 64.0
    rounds = {item.index: item for item in parsed.counted_rounds if item.is_formal}
    durations = {
        index: (item.end_tick - (item.freeze_end_tick or item.live_start)) / tickrate
        for index, item in rounds.items()
        if item.end_tick and (item.freeze_end_tick or item.live_start)
        and item.end_tick > (item.freeze_end_tick or item.live_start)
    }
    rel_durations = {i: d for i, d in durations.items() if i in reliable}

    kills: list[dict[str, Any]] = []
    death_times: dict[int, float] = {}
    awp_kills = 0
    for entry in getattr(parsed, "kill_feed", []) or []:
        if entry.get("bot_controlled"):
            continue
        ridx = entry.get("round_index")
        if ridx not in reliable:
            continue
        if entry.get("victim") == player.steamid and entry.get("t") is not None:
            death_times[ridx] = float(entry["t"])
        attacker = entry.get("attacker")
        if attacker == player.steamid and entry.get("victim") != player.steamid:
            kills.append(entry)
            if str(entry.get("weapon") or "").casefold() == "awp":
                awp_kills += 1

    rounds_played = max(1, int(getattr(player.raw, "rounds", 0) or len(rounds)))
    rel_rounds = max(1, len(reliable))

    raw: dict[str, Any] = {}
    axes: dict[str, float | None] = {}

    # --- aggression: opening-duel attempt rate + close engagement distance
    opening_attempts = int(getattr(player.raw, "opening_kills", 0)) + int(
        getattr(player.raw, "opening_deaths", 0)
    )
    open_rate = opening_attempts / rounds_played
    distances = [e["distance"] for e in kills if e.get("distance")]
    avg_distance = sum(distances) / len(distances) if distances else None
    distance_axis = 1.0 - min(avg_distance, _CAP_DISTANCE_M) / _CAP_DISTANCE_M if avg_distance is not None else None
    open_axis = _clamp01(open_rate / _CAP_OPEN_RATE)
    axes["aggression"] = _clamp01(
        0.6 * open_axis + 0.4 * distance_axis if distance_axis is not None else open_axis
    )
    raw["opening_attempts"] = opening_attempts
    raw["opening_rate"] = round(open_rate, 3)
    raw["avg_distance_m"] = round(avg_distance, 1) if avg_distance is not None else None

    # --- survival: survived-round share + relative lifetime inside a round
    survived = int(getattr(player.raw, "survived_rounds", 0))
    survive_share = survived / rounds_played
    lifetimes = []
    for ridx, dur in rel_durations.items():
        if dur <= 0:
            continue
        died = death_times.get(ridx)
        lifetimes.append(min(died, dur) / dur if died is not None else 1.0)
    lifetime_axis = sum(lifetimes) / len(lifetimes) if lifetimes else None
    axes["survival"] = _clamp01(
        0.5 * survive_share + 0.5 * lifetime_axis
        if lifetime_axis is not None
        else survive_share
    )
    raw["survived_rounds"] = survived
    raw["avg_lifetime_ratio"] = round(lifetime_axis, 3) if lifetime_axis is not None else None

    # --- late: share of kills in the final quarter of a round
    late_kills = 0
    for entry in kills:
        dur = durations.get(entry.get("round_index"))
        if dur and entry.get("t") is not None and entry["t"] >= _LATE_FRACTION * dur:
            late_kills += 1
    late_share = late_kills / len(kills) if kills else None
    axes["late"] = _clamp01(late_share / _CAP_LATE_SHARE) if late_share is not None else None
    raw["late_kill_share"] = round(late_share, 3) if late_share is not None else None

    # --- utility: grenades used per round (exact event counts)
    usage = (getattr(parsed, "utility_usage", {}) or {}).get(player.steamid, {})
    util_total = sum(int(v) for v in usage.values()) if usage else 0
    util_per_round = util_total / rounds_played
    axes["utility"] = _clamp01(util_per_round / _CAP_UTILITY_PER_ROUND)
    raw["utility_per_round"] = round(util_per_round, 3)
    raw["utility_detail"] = dict(usage)

    # --- timing: share of own kill/death events inside the first 20 seconds
    early = 0
    total_events = 0
    for entry in kills:
        if entry.get("t") is not None:
            total_events += 1
            if entry["t"] <= _EARLY_SECONDS:
                early += 1
    for ridx, died in death_times.items():
        total_events += 1
        if died <= _EARLY_SECONDS:
            early += 1
    early_share = early / total_events if total_events else None
    axes["timing"] = _clamp01(early_share) if early_share is not None else None
    raw["early_event_share"] = round(early_share, 3) if early_share is not None else None

    # --- roam: dispersion of own kill positions around their centroid
    points = [
        e["attacker_pos"]
        for e in kills
        if e.get("attacker_pos") and all(math.isfinite(v) for v in e["attacker_pos"])
    ]
    if len(points) >= 2:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        dispersion = (
            sum(math.hypot(p[0] - cx, p[1] - cy) for p in points) / len(points)
        ) / 39.3701  # game units -> metres
        axes["roam"] = _clamp01(dispersion / _CAP_ROAM_M)
        raw["position_dispersion_m"] = round(dispersion, 1)
    else:
        axes["roam"] = None
        raw["position_dispersion_m"] = None

    raw["awp_kills"] = awp_kills
    raw["kills_sampled"] = len(kills)
    coverage = {
        "reliable_rounds": len(reliable),
        "formal_rounds": len(rounds),
    }
    available = any(v is not None for v in axes.values())
    return {"axes": axes, "raw": raw, "coverage": coverage, "available": available}


def match_profiles(axes: dict[str, float | None]) -> list[dict[str, Any]]:
    """Independent similarity: exp(-4 * root mean squared feature distance)."""

    usable = [key for key, _ in AXES if axes.get(key) is not None]
    if not usable:
        return []
    scored = []
    for name, profile in PRO_PROFILES.items():
        dist = math.sqrt(sum((axes[key] - profile[key]) ** 2 for key in usable) / len(usable))
        scored.append((name, 1.0 - dist))
    scored.sort(key=lambda item: -item[1])
    return [
        {"name": name, "similarity": round(100.0 * math.exp(-4.0 * (1.0 - score)), 1),
         "score": round(score, 3)}
        for name, score in scored[:3]
    ]


def classify_archetype(axes: dict[str, float | None], raw: dict[str, Any]) -> str:
    kills_sampled = raw.get("kills_sampled") or 0
    if kills_sampled and (raw.get("awp_kills") or 0) / kills_sampled >= _AWP_SHARE:
        return "狙击核心"
    usable = [key for key, _ in AXES if axes.get(key) is not None]
    if not usable:
        return "未知"
    best = None
    for name, proto in ARCHETYPES.items():
        dist = math.sqrt(sum((axes[key] - proto[key]) ** 2 for key in usable) / len(usable))
        if best is None or dist < best[1]:
            best = (name, dist)
    return best[0]


def summarize(axes: dict[str, float | None], archetype: str) -> str:
    def level(key: str) -> str:
        value = axes.get(key)
        if value is None:
            return ""
        if value >= 0.66:
            return {"aggression": "高侵略", "survival": "高存活", "late": "后段发力",
                    "utility": "道具密集", "timing": "前期主动", "roam": "大范围游走"}[key]
        if value <= 0.33:
            return {"aggression": "低侵略", "survival": "低存活", "late": "前段收尾少",
                    "utility": "道具稀少", "timing": "前期保守", "roam": "站位固定"}[key]
        return ""

    parts = [level(key) for key, _ in AXES]
    traits = "、".join(part for part in parts if part)
    return f"{traits}的{archetype}型" if traits else f"{archetype}型"


def build_style_dna(parsed: Any, steamid: int) -> dict[str, Any]:
    features = compute_features(parsed, steamid)
    axes = features["axes"]
    archetype = classify_archetype(axes, features["raw"]) if features["available"] else "未知"
    return {
        "available": features["available"],
        "steamid": int(steamid),
        "axes": [
            {"key": key, "label": label, "value": axes.get(key)}
            for key, label in AXES
        ],
        "raw": features["raw"],
        "coverage": features["coverage"],
        "archetype": archetype,
        "summary": summarize(axes, archetype) if features["available"] else "",
        "matches": match_profiles(axes),
        "profiles": {
            name: [profile.get(key) for key, _ in AXES] for name, profile in PRO_PROFILES.items()
        },
    }
