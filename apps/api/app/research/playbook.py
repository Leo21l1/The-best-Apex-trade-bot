"""Strategy playbook generated from the code (specs, families, mandatory factors, level rules)."""

from __future__ import annotations

from app.services import setups
from app.strategies.library import STRATEGIES

FAMILY_TITLE = {
    "continuation": "Trend continuation",
    "breakout": "Breakout",
    "reversal": "Reversal",
    "smc": "Smart Money Concepts",
}

FAMILY_NOTES = {
    "continuation": "Trades with an established trend after a pullback. Premium/discount location works against "
    "it by construction (longs in an uptrend usually sit in the premium half): see hypothesis H1. Liquidity "
    "sweeps and zone retests are optional context, not its trigger.",
    "breakout": "Trades acceptance beyond a range or level. Its natural liquidity evidence is 'accepted beyond', "
    "not a sweep-and-reclaim; an FVG retest is optional. Nearby minor opposing zones often cap TP1: see H2.",
    "reversal": "Trades a failed move back into range. A sweep-and-reclaim and favourable location are central "
    "to the idea, so premium/discount stays required.",
    "smc": "Trades structure shifts and returns to FVGs / order blocks after liquidity is taken. Location and "
    "zone validity are central.",
}

LEVELS = (
    "Entry: the strategy's entry and zone. Stop: nearest structural invalidation beyond the zone (sweep extreme, "
    "the retested FVG/OB invalidation, 5m/15m pivot) at least max(4 ticks, 0.3 x ATR5m) away, minus a buffer "
    "max(2 ticks, 0.1 x ATR); otherwise the strategy stop. TP1/TP2: one tick before the nearest opposing liquidity, "
    "opposing FVG/OB or 15m pivot (H2: major levels only for continuation/breakout). Net R:R to TP1 >= 1.5."
)


def playbook_md() -> str:
    lines = [
        "# Strategy playbook",
        "",
        "Generated from the code (`python -m app.cli strategy-playbook`). Live rules are unchanged; H1-H4 are "
        "experimental research hypotheses evaluated only by `confluence-study`. "
        "H1: location optional for continuation/breakout. H2: major-level targets for continuation/breakout. "
        "H3: 15m internal-structure fallback for the bias. "
        "H4: the strategy's own stop with its second designed target as the exit.",
        "",
        "Every strategy shares: closed candles only; strategy triggers 09:35-15:30 ET; confirmation needs the New "
        "York session, no NEWS WINDOW ACTIVE / POST-NEWS VOLATILITY, a fresh calendar, 4/5 confluences, its "
        "mandatory factors, a valid structural plan and net R:R >= 1.5 to TP1; setups expire 30 minutes after "
        "confirmation and are invalidated by an opposite 5m structure shift before entry.",
        "",
        f"**Levels:** {LEVELS}",
        "",
        "**Historical evidence:** none of the eleven strategies is validated. The latest audited confluence "
        "studies (Dec 2025 - Sep 2026 development data) show negative or statistically inconclusive results "
        "after deduplication, one-position-at-a-time and the $1,000 risk cap. Earlier hypotheses (ES MTF Trend T1, "
        "NQ Breakout Retest T2) remain unvalidated hypotheses.",
        "",
    ]
    for fam, title in FAMILY_TITLE.items():
        lines += [f"## {title}", "", FAMILY_NOTES[fam], ""]
        for st in STRATEGIES:
            if setups.FAMILY_OF.get(st.spec.id) != fam:
                continue
            sp = st.spec
            mand = ", ".join(setups.FACTORS[i] for i in setups.MANDATORY.get(sp.id, ())) or "strategy trigger only"
            opt = ", ".join(v for k, v in setups.FACTORS.items() if k not in setups.MANDATORY.get(sp.id, ()))
            lines += [
                f"### {sp.name} (`{sp.id}`)",
                "",
                f"- **Regime:** {sp.regime}",
                f"- **Entry (trigger):** {sp.entry}",
                f"- **Mandatory confluence:** {mand} (plus the trigger)",
                f"- **Optional confluence:** {opt}",
                f"- **Invalidation:** {sp.invalidation}",
                f"- **Strategy stop / targets (research backtests):** {sp.stop} / {sp.targets}",
                "- **Setup-engine stop / targets (live):** structural, see Levels above.",
                f"- **Time exit:** {sp.time_exit}",
                "- **Hypotheses that apply:** "
                + ", ".join(
                    h
                    for h, ok in (
                        ("H1 (location optional)", fam in ("continuation", "breakout")),
                        ("H2 (major-level targets)", fam in ("continuation", "breakout")),
                        ("H3 (15m internal fallback)", True),
                        ("H4 (native stop + T2)", True),
                    )
                    if ok
                ),
                "- **Evidence:** RESEARCH / UNVALIDATED.",
                "",
            ]
    return "\n".join(lines) + "\n"
