"""Explore event-layer attribution quality on the user Mirage demo.

Compare three sources per player:
  A) scoreboard totals (demoparser2 kills_total/deaths_total at final tick)
  B) awpy demo.kills event attribution
  C) demoparser2 raw player_death event attribution
Also: per-round comparison of B and C against scoreboard round deltas.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEMO = r"D:\program files\steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays\auto-20261122-1713-de_mirage-Beneqin1.dem"

import demoparser2  # noqa: E402

parser = demoparser2.DemoParser(DEMO)

# Scoreboard totals at the final round_end tick.
ends = parser.parse_event("round_end").to_dict("records")
end_ticks = {}
for r in ends:
    rn = int(r.get("round") or 0)
    t = int(r.get("tick") or 0)
    if rn > 0 and t > 0:
        end_ticks[rn] = t
final_tick = max(end_ticks.values())
props = ["kills_total", "deaths_total", "assists_total", "team_num"]
frame = parser.parse_ticks(props, ticks=[final_tick])
sb = {}
for r in frame.to_dict("records"):
    if int(r["tick"]) != final_tick:
        continue
    sb[int(r["steamid"])] = r

print("=== scoreboard totals (final tick", final_tick, ") ===")
names = {}
for sid, r in sorted(sb.items()):
    print(f"{sid}  K={int(r['kills_total']):3d} D={int(r['deaths_total']):3d} A={int(r['assists_total']):3d}")

# demoparser2 raw player_death events
deaths_df = parser.parse_event("player_death")
print("\nplayer_death columns:", list(frame.columns)[:0], "->", list(deaths_df.columns))
events = deaths_df.to_dict("records")
print("total player_death events:", len(events))

c_kills = Counter()
c_deaths = Counter()
odd = 0
for e in events:
    att = e.get("attacker_steamid")
    vic = e.get("user_steamid")
    try:
        att = int(att) if att is not None else 0
    except (TypeError, ValueError):
        att = 0
    try:
        vic = int(vic) if vic is not None else 0
    except (TypeError, ValueError):
        vic = 0
    if vic:
        c_deaths[vic] += 1
    if att and att != vic and att in sb:
        c_kills[att] += 1
    else:
        odd += 1

print("\n=== demoparser2 event attribution vs scoreboard ===")
ok = True
for sid, r in sorted(sb.items()):
    ek, ed = c_kills.get(sid, 0), c_deaths.get(sid, 0)
    sk, sd = int(r["kills_total"]), int(r["deaths_total"])
    mark = "OK " if (ek == sk and ed == sd) else "DIFF"
    if ek != sk or ed != sd:
        ok = False
    print(f"{mark} {sid}  events K={ek:3d} D={ed:3d}   scoreboard K={sk:3d} D={sd:3d}")
print("world/suicide/unknown-attacker events:", odd)
print("demoparser2 events consistent with scoreboard:", ok)

# awpy kills table
print("\n=== awpy demo.kills attribution vs scoreboard ===")
from awpy import Demo  # noqa: E402

demo = Demo(DEMO)
ak = Counter()
ad = Counter()
for row in demo.kills.to_dicts():
    att = row.get("attacker_steamid")
    vic = row.get("victim_steamid")
    try:
        att = int(att) if att is not None else 0
    except (TypeError, ValueError):
        att = 0
    try:
        vic = int(vic) if vic is not None else 0
    except (TypeError, ValueError):
        vic = 0
    if vic:
        ad[vic] += 1
    if att and att != vic and att in sb:
        ak[att] += 1
for sid, r in sorted(sb.items()):
    ek, ed = ak.get(sid, 0), ad.get(sid, 0)
    sk, sd = int(r["kills_total"]), int(r["deaths_total"])
    mark = "OK " if (ek == sk and ed == sd) else "DIFF"
    print(f"{mark} {sid}  awpy K={ek:3d} D={ed:3d}   scoreboard K={sk:3d} D={sd:3d}")

# sample events columns for weapon / round info
print("\nsample player_death row keys:", sorted(events[0].keys()) if events else None)
if events:
    e = events[len(events) // 2]
    print({k: e.get(k) for k in ("tick", "attacker_steamid", "user_steamid", "weapon", "headshot", "assister_steamid") if k in e})
