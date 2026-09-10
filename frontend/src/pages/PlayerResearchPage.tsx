import { useMemo, useState } from "react";
import Disclaimer from "../components/Disclaimer";
import {
  buildScenarioProjection,
  MOCK_PLAYER_CONTEXT,
  ROLE_LABELS,
  TAG_LABELS,
  type PlayerRole,
  type TagAssumption,
  type TeammateStatus,
} from "../features/playerContext";
import "./PlayerResearchPage.css";

const pct = (value: number) => `${Math.round(value * 100)}%`;
const signed = (value: number) => `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;

function SplitCard({ status }: { status: TeammateStatus }) {
  const research = MOCK_PLAYER_CONTEXT;
  const split = research.splits[status];
  const isOut = status === "out";

  return (
    <article className={`research-split-card ${isOut ? "research-split-card--highlight" : ""}`}>
      <div className="research-split-card__heading">
        <div>
          <span className="research-eyebrow">{research.teammate.shortName}</span>
          <h3>{isOut ? "Out of the team" : "In the team"}</h3>
        </div>
        <span className="chip chip--neutral">{split.games} games</span>
      </div>
      <strong className="research-split-card__average num">{split.average.toFixed(1)}</strong>
      <span className="research-split-card__average-label">average disposals</span>
      <dl className="research-mini-stats">
        <div><dt>Median</dt><dd>{split.median.toFixed(0)}</dd></div>
        <div><dt>25+ rate</dt><dd>{pct(split.hitRate25)}</dd></div>
        <div><dt>30+ rate</dt><dd>{pct(split.hitRate30)}</dd></div>
        <div><dt>Avg TOG</dt><dd>{split.averageTog}%</dd></div>
      </dl>
    </article>
  );
}

function PlayerResearchPage() {
  const research = MOCK_PLAYER_CONTEXT;
  const [teammateStatus, setTeammateStatus] = useState<TeammateStatus>("in");
  const [role, setRole] = useState<PlayerRole>("mixed");
  const [tagAssumption, setTagAssumption] = useState<TagAssumption>("possible");
  const [evidenceFilter, setEvidenceFilter] = useState<"all" | TeammateStatus>("all");

  const scenario = useMemo(
    () => buildScenarioProjection(research, { teammateStatus, role, tagAssumption }),
    [teammateStatus, role, tagAssumption, research],
  );
  const evidence = evidenceFilter === "all"
    ? research.evidence
    : research.evidence.filter((game) => game.teammateStatus === evidenceFilter);

  return (
    <main className="player-research-page">
      <div className="research-prototype-banner">
        <span>Interactive prototype</span>
        All figures on this page are mock data. They demonstrate the intended analysis and are not betting advice.
      </div>

      <header className="research-header">
        <div className="research-player-avatar" aria-hidden="true">{research.player.initials}</div>
        <div className="research-header__copy">
          <span className="research-eyebrow">Player context lab</span>
          <h1>{research.player.name}</h1>
          <p>{research.player.team} · vs {research.upcomingMatch.opponent} · {research.upcomingMatch.venue}</p>
        </div>
        <div className="research-header__projection">
          <span>Base projection</span>
          <strong className="num">{research.baselineProjection.toFixed(1)}</strong>
          <small>disposals</small>
        </div>
      </header>

      <section className="research-question-card">
        <div>
          <span className="research-eyebrow">Start with a question</span>
          <h2>What changes when {research.teammate.name} plays?</h2>
          <p>Compare the raw split, inspect every game, then test how much of the difference remains after context is considered.</p>
        </div>
        <label className="research-select-field">
          Teammate
          <select value={research.teammate.id} disabled aria-label="Selected teammate">
            <option value={research.teammate.id}>{research.teammate.name}</option>
          </select>
        </label>
      </section>

      <section aria-labelledby="split-heading">
        <div className="section-row research-section-heading">
          <div>
            <h2 id="split-heading" className="section-title">With and without comparison</h2>
            <p className="hint">Raw historical split · same player, different team availability</p>
          </div>
          <span className="chip chip--warning">Small-sample warning</span>
        </div>
        <div className="research-split-grid">
          <SplitCard status="in" />
          <div className="research-split-difference" aria-label="Raw average difference">
            <span>Raw difference</span>
            <strong className="num">+{(research.splits.out.average - research.splits.in.average).toFixed(1)}</strong>
            <small>disposals</small>
          </div>
          <SplitCard status="out" />
        </div>
        <div className="research-adjusted-note">
          <span className="research-adjusted-note__icon" aria-hidden="true">↳</span>
          <div>
            <strong>Adjusted estimate: +{research.adjustedTeammateImpact.toFixed(1)} disposals</strong>
            <p>After accounting for role, opponent strength, venue and recent form, the estimated difference is smaller than the raw split. This is association, not proof that the absence causes the change.</p>
          </div>
        </div>
      </section>

      <section className="research-main-grid">
        <div className="card research-scenario-card">
          <div className="research-section-heading">
            <span className="research-eyebrow">Scenario builder</span>
            <h2 className="section-title">Set the likely match conditions</h2>
            <p className="hint">Change an assumption to see its isolated effect.</p>
          </div>

          <fieldset className="research-control-group">
            <legend>{research.teammate.shortName}</legend>
            <div className="research-segmented-control">
              <button type="button" className={teammateStatus === "in" ? "is-active" : ""} onClick={() => setTeammateStatus("in")}>Playing</button>
              <button type="button" className={teammateStatus === "out" ? "is-active" : ""} onClick={() => setTeammateStatus("out")}>Out</button>
            </div>
          </fieldset>

          <fieldset className="research-control-group">
            <legend>Expected role</legend>
            <div className="research-segmented-control research-segmented-control--three">
              {(Object.keys(ROLE_LABELS) as PlayerRole[]).map((value) => (
                <button key={value} type="button" className={role === value ? "is-active" : ""} onClick={() => setRole(value)}>{ROLE_LABELS[value]}</button>
              ))}
            </div>
          </fieldset>

          <fieldset className="research-control-group">
            <legend>Tagging assumption</legend>
            <div className="research-segmented-control research-segmented-control--three">
              {(Object.keys(TAG_LABELS) as TagAssumption[]).map((value) => (
                <button key={value} type="button" className={tagAssumption === value ? "is-active" : ""} onClick={() => setTagAssumption(value)}>{TAG_LABELS[value]}</button>
              ))}
            </div>
          </fieldset>
        </div>

        <div className="card research-output-card" aria-live="polite">
          <span className="research-eyebrow">Scenario projection</span>
          <div className="research-output-card__headline">
            <div>
              <strong className="num">{scenario.expected.toFixed(1)}</strong>
              <span>expected disposals</span>
            </div>
            <span className={`research-change ${scenario.change >= 0 ? "research-change--positive" : "research-change--negative"}`}>
              {signed(scenario.change)} vs base
            </span>
          </div>
          <div className="research-range">
            <span>Likely range</span>
            <strong className="num">{scenario.range[0].toFixed(0)}–{scenario.range[1].toFixed(0)}</strong>
          </div>
          <div className="research-milestones">
            {scenario.probabilities.map((item) => (
              <div key={item.threshold}>
                <span>{item.threshold}+</span>
                <strong className="num">{pct(item.probability)}</strong>
                <div className="research-probability-track"><i style={{ width: pct(item.probability) }} /></div>
              </div>
            ))}
          </div>
          <div className="research-adjustment-list">
            {scenario.adjustments.map((adjustment) => (
              <div key={adjustment.label}>
                <span>{adjustment.label}</span>
                <strong className={adjustment.value > 0 ? "is-positive" : adjustment.value < 0 ? "is-negative" : ""}>{signed(adjustment.value)}</strong>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="card research-tag-card">
        <div className="research-tag-card__score">
          <span className="research-eyebrow">Tag watch</span>
          <strong>{pct(research.tagEstimate.probability)}</strong>
          <span>prototype likelihood</span>
          <small>{research.tagEstimate.confidence} confidence</small>
        </div>
        <div className="research-tag-card__body">
          <h2 className="section-title">How likely is direct attention?</h2>
          <p className="hint">This estimate should only become a live feature once tagging labels can be verified consistently.</p>
          <ul>
            {research.tagEstimate.factors.map((factor) => (
              <li key={factor.label}>
                <span className={`research-direction research-direction--${factor.direction}`} aria-hidden="true">
                  {factor.direction === "up" ? "↑" : factor.direction === "down" ? "↓" : "→"}
                </span>
                <div><strong>{factor.label}</strong><p>{factor.detail}</p></div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section aria-labelledby="evidence-heading">
        <div className="section-row research-section-heading research-evidence-heading">
          <div>
            <h2 id="evidence-heading" className="section-title">Inspect the evidence</h2>
            <p className="hint">Every game behind the split should remain visible and auditable.</p>
          </div>
          <div className="research-filter-buttons" aria-label="Filter evidence games">
            {(["all", "in", "out"] as const).map((value) => (
              <button key={value} type="button" className={evidenceFilter === value ? "is-active" : ""} onClick={() => setEvidenceFilter(value)}>
                {value === "all" ? "All games" : value === "in" ? "Teammate in" : "Teammate out"}
              </button>
            ))}
          </div>
        </div>
        <div className="table-scroll research-evidence-table-wrap">
          <table className="data-table research-evidence-table">
            <thead><tr><th>Match</th><th>Venue</th><th>Teammate</th><th>Role</th><th>Tag signal</th><th className="num">Disposals</th></tr></thead>
            <tbody>
              {evidence.map((game) => (
                <tr key={game.id}>
                  <td><strong>{game.round}</strong><span>vs {game.opponent}</span></td>
                  <td>{game.venue}</td>
                  <td><span className={`chip ${game.teammateStatus === "in" ? "chip--success" : "chip--neutral"}`}>{game.teammateStatus === "in" ? "In" : "Out"}</span></td>
                  <td>{game.role}</td>
                  <td>{game.tag}</td>
                  <td className="num"><strong>{game.disposals}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <Disclaimer />
    </main>
  );
}

export default PlayerResearchPage;

