from __future__ import annotations

import hashlib
import math
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from .contracts import MarketData


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

    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    ordered_dates = tuple(sorted(prices))
    return MarketData(
        dates=ordered_dates,
        prices={trading_date: prices[trading_date] for trading_date in ordered_dates},
        fund_names=fund_names,
        source_sha256=source_sha256,
    )
