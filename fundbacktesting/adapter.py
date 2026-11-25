from __future__ import annotations

import csv
import hashlib
import math
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from .contracts import FundMeta, MarketData


def _as_date(value: object, epoch: datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        converted = from_excel(value, epoch=epoch)
        if isinstance(converted, datetime):
            return converted.date()
    raise ValueError(f"Unsupported date value in workbook: {value!r}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_market_data(path: str | Path, sheet_name: str | None = None) -> MarketData:
    """Load the sample workbook layout into source-independent daily NAV data."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Market data file does not exist: {source}")

    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        if sheet_name is None:
            worksheet = workbook[workbook.sheetnames[0]]
        elif sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
        else:
            raise ValueError(
                f"Sheet {sheet_name!r} not found. Available sheets: "
                f"{', '.join(workbook.sheetnames)}"
            )

        header_names = next(
            worksheet.iter_rows(min_row=4, max_row=4, values_only=True), ()
        )
        header_codes = next(
            worksheet.iter_rows(min_row=5, max_row=5, values_only=True), ()
        )
        width = max(len(header_names), len(header_codes))
        fund_names: dict[str, str] = {}
        fund_columns: list[tuple[int, str]] = []
        for column in range(1, width):
            name = header_names[column] if column < len(header_names) else None
            code = header_codes[column] if column < len(header_codes) else None
            if code is None or str(code).strip() == "":
                continue
            normalized_code = str(code).strip()
            if normalized_code in fund_names:
                raise ValueError(f"Duplicate fund code in workbook: {normalized_code}")
            fund_names[normalized_code] = (
                str(name).strip() if name is not None else normalized_code
            )
            fund_columns.append((column, normalized_code))
        if not fund_names:
            raise ValueError("No fund codes found on workbook row 5.")

        prices: dict[date, dict[str, float]] = {}
        for row_number, row in enumerate(
            worksheet.iter_rows(min_row=6, max_col=width, values_only=True),
            start=6,
        ):
            raw_date = row[0] if row else None
            if raw_date is None or str(raw_date).strip() == "":
                continue
            trading_date = _as_date(raw_date, workbook.epoch)
            if trading_date in prices:
                raise ValueError(f"Duplicate trading date in workbook: {trading_date}")

            daily_prices: dict[str, float] = {}
            for column, code in fund_columns:
                raw_price = row[column] if column < len(row) else None
                if raw_price is None or str(raw_price).strip() == "":
                    continue
                if isinstance(raw_price, bool):
                    raise ValueError(
                        f"Invalid NAV at row {row_number}, fund {code}: {raw_price!r}"
                    )
                try:
                    price = float(raw_price)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"Invalid NAV at row {row_number}, fund {code}: {raw_price!r}"
                    ) from error
                if not math.isfinite(price) or price <= 0:
                    raise ValueError(
                        f"NAV must be finite and positive at row {row_number}, "
                        f"fund {code}: {price!r}"
                    )
                daily_prices[code] = price
            if daily_prices:
                prices[trading_date] = daily_prices
    finally:
        workbook.close()

    if not prices:
        raise ValueError("No dated NAV observations found in the workbook.")

    source_sha256 = _sha256(source)
    ordered_dates = tuple(sorted(prices))
    return MarketData(
        dates=ordered_dates,
        prices={trading_date: prices[trading_date] for trading_date in ordered_dates},
        fund_names=fund_names,
        source_sha256=source_sha256,
        source_fingerprints={source.name: source_sha256},
    )


def load_nav_csv(path: str | Path) -> MarketData:
    """Load a wide NAV CSV: first column is date, remaining columns are fund NAVs.

    Missing cells are skipped, never interpolated. A fund may begin later than the
    first date; the returned data simply has no observation for that date.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"NAV CSV does not exist: {source}")

    with source.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.reader(file))
    if not rows:
        raise ValueError(f"NAV CSV is empty: {source}")

    header = [cell.strip() for cell in rows[0]]
    if not header or header[0] not in ("日期", "date", "Date"):
        raise ValueError("NAV CSV first header cell must be a date column.")
    fund_codes = [code for code in header[1:] if code]
    if not fund_codes:
        raise ValueError("NAV CSV has no fund columns.")

    prices: dict[date, dict[str, float]] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        if not row or not row[0].strip():
            continue
        raw_date = row[0].strip()[:10]
        try:
            trading_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError(f"Invalid date at row {row_number}: {raw_date!r}") from error
        if trading_date in prices:
            raise ValueError(f"Duplicate trading date in NAV CSV: {trading_date}")

        daily_prices: dict[str, float] = {}
        for index, code in enumerate(fund_codes, start=1):
            raw_value = row[index].strip() if index < len(row) else ""
            if not raw_value:
                continue
            try:
                value = float(raw_value)
            except ValueError as error:
                raise ValueError(
                    f"Invalid NAV at row {row_number}, fund {code}: {raw_value!r}"
                ) from error
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"NAV must be finite and positive at row {row_number}, "
                    f"fund {code}: {value!r}"
                )
            daily_prices[code] = value
        if daily_prices:
            prices[trading_date] = daily_prices

    if not prices:
        raise ValueError(f"NAV CSV has no dated observations: {source}")

    ordered_dates = tuple(sorted(prices))
    fingerprint = _sha256(source)
    return MarketData(
        dates=ordered_dates,
        prices={item: prices[item] for item in ordered_dates},
        fund_names={code: code for code in fund_codes},
        source_sha256=fingerprint,
        source_fingerprints={source.name: fingerprint},
    )


def load_risk_free_csv(path: str | Path, input_is_percent: bool = True) -> dict[date, float]:
    """Load a daily risk-free rate CSV. Values are converted to decimals by default."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Risk-free rate CSV does not exist: {source}")

    rates: dict[date, float] = {}
    with source.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        header = next(reader, None)
        if header is None or header[0].strip().lower() != "date":
            raise ValueError("Risk-free CSV first header cell must be 'date'.")
        for row_number, row in enumerate(reader, start=2):
            if not row or not row[0].strip():
                continue
            try:
                day = date.fromisoformat(row[0].strip()[:10])
            except ValueError as error:
                raise ValueError(
                    f"Invalid date at row {row_number}: {row[0]!r}"
                ) from error
            try:
                value = float(row[1])
            except (IndexError, ValueError) as error:
                raise ValueError(f"Invalid rate at row {row_number}: {row!r}") from error
            if not math.isfinite(value):
                raise ValueError(f"Rate must be finite at row {row_number}: {value!r}")
            rates[day] = value / 100.0 if input_is_percent else value
    if not rates:
        raise ValueError(f"Risk-free CSV has no observations: {source}")
    return rates


def load_instrument_meta_csv(path: str | Path) -> dict[str, FundMeta]:
    """Load optional instrument metadata: code,product_type,inception_date,eligibility_start,name."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Instrument metadata CSV does not exist: {source}")

    metadata: dict[str, FundMeta] = {}
    with source.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "code" not in reader.fieldnames:
            raise ValueError("Instrument metadata CSV requires a 'code' column.")
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            inception = (row.get("inception_date") or "").strip()
            eligibility = (row.get("eligibility_start") or "").strip()
            metadata[code] = FundMeta(
                product_type=(row.get("product_type") or "").strip() or None,
                inception_date=date.fromisoformat(inception) if inception else None,
                eligibility_start=(
                    date.fromisoformat(eligibility) if eligibility else None
                ),
                name=(row.get("name") or "").strip() or None,
            )
    return metadata


def with_market_context(
    market_data: MarketData,
    *,
    risk_free_rates: dict[date, float] | None = None,
    fund_meta: dict[str, FundMeta] | None = None,
    extra_fingerprints: dict[str, str] | None = None,
) -> MarketData:
    """Return a new MarketData with optional context attached, leaving the input intact."""
    fingerprints = dict(market_data.source_fingerprints or {})
    fingerprints.update(extra_fingerprints or {})
    return MarketData(
        dates=market_data.dates,
        prices=market_data.prices,
        fund_names=market_data.fund_names,
        source_sha256=market_data.source_sha256,
        risk_free_rates=risk_free_rates,
        fund_meta=fund_meta,
        source_fingerprints=fingerprints,
    )
