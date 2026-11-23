"""Probe demoparser2/awpy capabilities needed for the 1.3.0 iteration."""
from __future__ import annotations

DEMO = r"D:\program files\steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays\auto-20261122-1713-de_mirage-Beneqin1.dem"


def main() -> None:
    import demoparser2

    parser = demoparser2.DemoParser(DEMO)

    # 1) round_mvp event
    try:
        frame = parser.parse_event("round_mvp")
        rows = frame.to_dict("records")
        print("round_mvp rows:", len(rows))
        print("round_mvp sample:", rows[:3])
    except Exception as exc:
        print("round_mvp FAILED:", exc)

    # 2) bot-control props
    for prop in ("m_bControllingBot", "m_iControlledBotEntIndex", "m_hPlayerPawn"):
        try:
            frame = parser.parse_ticks([prop], ticks=[5000, 20000])
            recs = frame.to_dict("records")
            vals = {str(r.get(prop)) for r in recs[:20]}
            print(f"prop {prop}: OK rows={len(recs)} sample={list(vals)[:5]}")
        except Exception as exc:
            print(f"prop {prop}: FAILED: {exc}")

    # 3) grenade detonation events
    for event in ("hegrenade_detonate", "flashbang_detonate", "smokegrenade_detonate", "inferno_startburn"):
        try:
            frame = parser.parse_event(event)
            rows = frame.to_dict("records")
            cols = list(rows[0].keys()) if rows else []
            print(f"event {event}: rows={len(rows)} cols={cols[:12]}")
        except Exception as exc:
            print(f"event {event}: FAILED: {exc}")

    # 4) awpy kills columns (positions?)
    from awpy import Demo

    demo = Demo(DEMO)
    kills = demo.kills.to_dicts()
    if kills:
        cols = sorted(kills[0].keys())
        pos = [c for c in cols if c.endswith(("_X", "_Y", "_Z")) or c.lower().endswith(("x", "y", "z"))]
        print("awpy kills columns:", cols)
        print("position-ish columns:", pos)
    # 5) demo.players columns (is_bot?)
    players = demo.players.to_dicts()
    if players:
        print("awpy players columns:", sorted(players[0].keys()))
    # 6) header tickrate
    print("tick_rate:", demo.tick_rate)


if __name__ == "__main__":
    main()
