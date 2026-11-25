"""研究实例的数据装配：把三份输入文件组装成框架通用的 MarketData。

本模块属于用例层，不是框架核心。它读取的数据格式与具体研究相关，
但产出的 MarketData 是框架通用类型。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fundbacktesting.adapter import (
    load_instrument_meta_csv,
    load_nav_csv,
    load_risk_free_csv,
    with_market_context,
)
from fundbacktesting.contracts import FundMeta, MarketData

DEFAULT_INPUT_DIR = Path(__file__).resolve().parents[2] / "data"


def build_market_data(input_dir: str | Path | None = None) -> MarketData:
    root = Path(input_dir) if input_dir is not None else DEFAULT_INPUT_DIR
    nav_public = load_nav_csv(root / "nav_pub119.csv")
    nav_ins = load_nav_csv(root / "nav5c.csv")

    all_dates = tuple(sorted(set(nav_public.dates) | set(nav_ins.dates)))
    prices: dict[date, dict[str, float]] = {}
    for trading_date in all_dates:
        row = dict(nav_public.prices.get(trading_date, {}))
        row.update(nav_ins.prices.get(trading_date, {}))
        prices[trading_date] = row

    fund_names = {**nav_public.fund_names, **nav_ins.fund_names}

    meta_path = root / "instrument_meta.csv"
    if meta_path.is_file():
        fund_meta = load_instrument_meta_csv(meta_path)
    else:
        inception_dates: dict[str, date] = {}
        for source in (nav_public, nav_ins):
            for code in source.fund_names:
                dates = [day for day, prices in source.prices.items() if code in prices]
                if dates:
                    inception_dates[code] = min(dates)
        fund_meta = {
            code: FundMeta(
                product_type="public_fund",
                inception_date=inception_dates.get(code),
            )
            for code in nav_public.fund_names
        }
        fund_meta.update(
            {
                code: FundMeta(
                    product_type="insurance_asset_management",
                    inception_date=inception_dates.get(code),
                )
                for code in nav_ins.fund_names
            }
        )

    combined = MarketData(
        dates=all_dates,
        prices=prices,
        fund_names=fund_names,
        source_sha256="combined:" + nav_public.source_sha256[:12] + nav_ins.source_sha256[:12],
    )
    return with_market_context(
        combined,
        risk_free_rates=load_risk_free_csv(root / "rf1y.csv"),
        fund_meta=fund_meta,
        extra_fingerprints={
            **dict(nav_public.source_fingerprints or {}),
            **dict(nav_ins.source_fingerprints or {}),
            "rf1y.csv": __import__("hashlib").sha256(
                (root / "rf1y.csv").read_bytes()
            ).hexdigest(),
        },
    )
