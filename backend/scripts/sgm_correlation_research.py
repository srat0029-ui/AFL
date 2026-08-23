"""Research script (B2B Pricing Engine, items 10-12): measures real
historical correlations between same-game outcomes, as evidence for
whether/how a future joint (same-game-multi) probability model would be
worth building. RESEARCH ONLY — deploys nothing, trains nothing, changes
no live model or ranking.

Uses Spearman rank correlation (not just Pearson — outcome distributions
here are skewed/count-valued, and the question that matters for pricing
is monotonic co-movement, not linear covariance) computed on the full
historical PlayerMatchStat/Match record, not a live/current sample.
"""

import itertools

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match, MatchStatus, PlayerMatchStat


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    db = SessionLocal()
    try:
        stats = db.execute(
            select(
                PlayerMatchStat.match_id, PlayerMatchStat.player_id, PlayerMatchStat.team_id,
                PlayerMatchStat.disposals, PlayerMatchStat.goals,
            )
        ).all()
        stats_df = pd.DataFrame(stats, columns=["match_id", "player_id", "team_id", "disposals", "goals"])

        matches = db.execute(
            select(Match.id, Match.home_team_id, Match.away_team_id, Match.home_score, Match.away_score)
            .where(Match.status == MatchStatus.COMPLETED)
        ).all()
        matches_df = pd.DataFrame(matches, columns=["match_id", "home_team_id", "away_team_id", "home_score", "away_score"])
        return stats_df, matches_df
    finally:
        db.close()


def _spearman(a, b, label: str) -> None:
    mask = ~(np.isnan(a) | np.isnan(b))
    a, b = np.asarray(a)[mask], np.asarray(b)[mask]
    if len(a) < 30:
        print(f"  {label}: n={len(a)} — too small to report")
        return
    rho, p = spearmanr(a, b)
    print(f"  {label}: rho={rho:+.3f}  p={p:.2g}  n={len(a):,}")


def main() -> None:
    stats_df, matches_df = load_data()
    matches_df["total_score"] = matches_df["home_score"] + matches_df["away_score"]
    matches_df["home_margin"] = matches_df["home_score"] - matches_df["away_score"]

    merged = stats_df.merge(matches_df, on="match_id", how="inner")
    merged["team_margin"] = np.where(merged["team_id"] == merged["home_team_id"], merged["home_margin"], -merged["home_margin"])

    print(f"n player-match rows: {len(merged):,} across {merged['match_id'].nunique():,} matches\n")

    print("1) player disposals <-> own team's match margin")
    _spearman(merged["disposals"], merged["team_margin"], "disposals vs team_margin")

    print("\n2) player goals <-> own team's total score")
    own_team_score = np.where(merged["team_id"] == merged["home_team_id"], merged["home_score"], merged["away_score"])
    _spearman(merged["goals"], own_team_score, "goals vs own_team_score")

    print("\n3) player goals <-> match total score")
    _spearman(merged["goals"], merged["total_score"], "goals vs match_total")

    # --- 4) player disposals <-> TEAMMATE disposals (same match, same team, different player) ---
    print("\n4) player disposals <-> teammate disposals (paired, same match/team)")
    teammate_pairs_disp = []
    for (match_id, team_id), grp in merged.groupby(["match_id", "team_id"]):
        d = grp["disposals"].to_numpy()
        if len(d) < 2:
            continue
        # every unordered pair within this team-match contributes one observation of (player_i, player_j)
        for i, j in itertools.combinations(range(len(d)), 2):
            teammate_pairs_disp.append((d[i], d[j]))
    if teammate_pairs_disp:
        a, b = zip(*teammate_pairs_disp)
        _spearman(pd.Series(a), pd.Series(b), "player_disposals vs teammate_disposals (all pairs)")
        print(f"  NOTE: {len(teammate_pairs_disp):,} pairs from {merged.groupby(['match_id','team_id']).ngroups:,} team-matches — heavily pseudo-replicated (multiple pairs per team-match share the same match-level noise); treat as descriptive only, not an independent-observations test (see item 7).")

    # --- 5) opposing-player disposals (same match, different team) ---
    print("\n5) player disposals <-> OPPOSING player disposals (paired, same match, different team)")
    opp_pairs = []
    for match_id, grp in merged.groupby("match_id"):
        home = grp[grp["team_id"] == grp["home_team_id"]]["disposals"].to_numpy()
        away = grp[grp["team_id"] == grp["away_team_id"]]["disposals"].to_numpy()
        if len(home) == 0 or len(away) == 0:
            continue
        for h in home:
            for aw in away:
                opp_pairs.append((h, aw))
    if opp_pairs:
        a, b = zip(*opp_pairs)
        # Subsampled - full cross product is enormous and each match contributes ~44x44 pairs, wildly pseudo-replicated.
        idx = np.random.RandomState(42).choice(len(a), size=min(50000, len(a)), replace=False)
        a_s, b_s = np.array(a)[idx], np.array(b)[idx]
        _spearman(pd.Series(a_s), pd.Series(b_s), "own_disposals vs opposing_player_disposals (subsampled)")

    # --- 6) multiple forwards scoring: within a team-match, correlation between one player scoring and TEAMMATE goal-scoring rate ---
    print("\n6) 'multiple forwards scoring' — within a team-match, does one player scoring 2+ associate with more teammates also scoring?")
    rows = []
    for (match_id, team_id), grp in merged.groupby(["match_id", "team_id"]):
        goals = grp["goals"].to_numpy()
        if len(goals) < 3:
            continue
        for i in range(len(goals)):
            this_scored_2plus = goals[i] >= 2
            others_scoring_count = int((np.delete(goals, i) >= 1).sum())
            rows.append((this_scored_2plus, others_scoring_count))
    if rows:
        flag, others = zip(*rows)
        flag_arr, others_arr = np.array(flag, dtype=float), np.array(others, dtype=float)
        rho, p = spearmanr(flag_arr, others_arr)
        with_2plus = others_arr[flag_arr == 1].mean()
        without = others_arr[flag_arr == 0].mean()
        print(f"  rho={rho:+.3f} p={p:.2g} n={len(rows):,}")
        print(f"  avg teammates also scoring when this player scored 2+: {with_2plus:.2f} vs {without:.2f} otherwise")

    print("\nDone. Research only — no model trained or deployed.")


if __name__ == "__main__":
    main()
