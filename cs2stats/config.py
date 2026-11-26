"""Application constants.

The exact statistics (K/D/A, ADR, KAST) are rebuilt from formal-round
``player_death`` / ``damage`` events.  ``opening_kills`` is a labelled
reference field.  Everything under the "approximate" section is our own
approximation and is labelled as such in the report.
"""
from __future__ import annotations

APP_NAME = "CS2 Stats Report"
APP_VERSION = "1.5.0"
TARGET_DEFAULT = "Beneqin1"

SIDE_T = "T"
SIDE_CT = "CT"
SIDES = (SIDE_T, SIDE_CT)
SIDE_LABEL = {SIDE_T: "T", SIDE_CT: "CT"}

TEAM_COLORS = {
    "A": "#98ffff",  # HLTV-like cyan
    "B": "#ffff00",  # HLTV-like yellow
}

# Rating 2.0 approximation coefficients (the public formula circulating with
# the HLTV Rating 2.0 announcement).  KAST enters as a percentage (0-100).
# These numbers are NOT HLTV's official implementation; the report labels the
# result as approximate.
RATING_KAST_COEF = 0.0073
RATING_KPR_COEF = 0.3591
RATING_DPR_COEF = -0.5329
RATING_IMPACT_COEF = 0.2372
RATING_ADR_COEF = 0.0032
RATING_INTERCEPT = 0.1587
# Soft cap: the public approximation overshoots on stomps (KPR/DPR extremes),
# so values above the threshold are compressed linearly.  A raw 2.35 becomes
# ~2.30; ratings at or below 2.0 are untouched.
RATING_SOFT_THRESHOLD = 2.0
RATING_SOFT_SLOPE = 0.85

# Impact: the classic 2.13*KPR + 0.42*APR - 0.41 base, plus explicit opening
# kill and multi-kill round weighting (per round, labelled approximate).
IMPACT_KPR_COEF = 2.13
IMPACT_APR_COEF = 0.42
IMPACT_INTERCEPT = -0.41
IMPACT_OPENING_COEF = 0.50
IMPACT_MULTIKILL_COEF = 0.30

ECON_KILL_COEF = 0.30
ECON_KILL_MIN = 0.70
ECON_KILL_MAX = 1.45
ECON_DEATH_COEF = 0.15
ECON_DEATH_MIN = 0.85
ECON_DEATH_MAX = 1.15
ECON_RATING_COEF = 0.12
ECON_DEATH_RATING_COEF = 0.10
ECON_RATING_LIMIT = 0.20

SWING_CALIBRATION = 3.0
SWING_PER_ROUND = 16.0
SWING_PCT_LIMIT = 25.0

MK_RATING_BONUS = 0.20

# Fixed display scale: pro reference at 2/3 radius, outer ring at 1.5x.
RADAR_PRO_AVG = {
    "kpr": 0.72,
    "rating": 1.06,
    "impact": 1.30,
    "adr": 78.0,
    "kast": 72.0,
    "survival": 36.0,
}
RADAR_MAX = {key: avg * 1.5 for key, avg in RADAR_PRO_AVG.items()}
RADAR_AXES = [
    ("kpr", "KPR", 2),
    ("rating", "Rating", 2),
    ("impact", "Impact", 2),
    ("adr", "ADR", 1),
    ("kast", "KAST", 1),
    ("survival", "Surviving", 1),
]

# (metric key, label, decimals, bar colour)
COMPARE_METRICS = [
    ("kpr", "KPR", 2, "#18b6c9"),
    ("dpr", "DPR", 2, "#18b6c9"),
    ("kast", "KAST", 1, "#b6e800"),
    ("mk_rating", "MK rating", 2, "#18b6c9"),
    ("swing_pct", "Swing", 2, "#18b6c9"),
    ("adr", "ADR", 1, "#18b6c9"),
    ("rating", "Rating", 2, "#3b82f6"),
]

APPROX_NOTE = "近似实现，非 HLTV 官方数值，不可用于与 HLTV 页面逐位比对"
