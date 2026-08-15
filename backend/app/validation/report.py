"""Result types for the AFL data-validation system, plus a pure text
formatter. Deliberately has no knowledge of the CLI (no argparse, no
sys.exit) so it can be built and inspected directly from tests or other
tooling — see app/ingestion/cli.py for the one place that decides what to
do with a report (print it, choose an exit code)."""

from dataclasses import dataclass, field
from enum import Enum


class Level(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    level: Level
    category: str
    message: str


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)
    # Readable "2016: 207 matches" style lines, separate from results
    # because it's a summary view over the same data, not a pass/fail check.
    season_summary: list[str] = field(default_factory=list)
    # "2016: 195/207 matches (94%)" — advanced team-stats coverage per season.
    team_stats_coverage: list[str] = field(default_factory=list)

    def add(self, level: Level, category: str, message: str) -> None:
        self.results.append(CheckResult(level, category, message))

    @property
    def has_failures(self) -> bool:
        return any(r.level == Level.FAIL for r in self.results)

    @property
    def has_warnings(self) -> bool:
        return any(r.level == Level.WARNING for r in self.results)

    def count(self, level: Level) -> int:
        return sum(1 for r in self.results if r.level == level)


def format_report(report: ValidationReport) -> str:
    lines = ["=== AFL Data Validation ==="]
    categories = sorted({r.category for r in report.results})
    for category in categories:
        lines.append(f"\n[{category}]")
        for r in report.results:
            if r.category == category:
                lines.append(f"  {r.level.value:<7} {r.message}")

    if report.season_summary:
        lines.append("\n[season summary]")
        for line in report.season_summary:
            lines.append(f"  {line}")

    if report.team_stats_coverage:
        lines.append("\n[advanced team-stats coverage]")
        for line in report.team_stats_coverage:
            lines.append(f"  {line}")

    lines.append(
        f"\n{report.count(Level.PASS)} passed, "
        f"{report.count(Level.WARNING)} warnings, "
        f"{report.count(Level.FAIL)} failures"
    )
    return "\n".join(lines)
