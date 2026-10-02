from datetime import datetime

from pydantic import BaseModel


class NbaDatasetStatusRead(BaseModel):
    key: str
    label: str
    rows: int
    latest_at: datetime | None


class NbaStatusRead(BaseModel):
    sport: str
    markets: list[str]
    datasets: list[NbaDatasetStatusRead]
    predictions_frozen: int
    predictions_with_closing_line: int
    predictions_settled: int
