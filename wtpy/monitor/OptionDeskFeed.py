"""Validate the requested market scope against server-owned contract metadata."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

from .AssetMarkets import FUTURE, instrument
from .OptionChain import chain


class Selection(BaseModel):
    underlying: str = Field(max_length=40)
    symbols: list[str] = Field(min_length=1,max_length=96)


class SurfacePoint(BaseModel):
    strike: float = Field(gt=0,le=1e9,allow_inf_nan=False)
    date: int = Field(ge=19900101,le=21001231)


class Spread(BaseModel):
    first: str = Field(max_length=48)
    second: str = Field(max_length=48)
    ratio: float = Field(gt=0,le=1e6,allow_inf_nan=False)
    strike: float = Field(ge=-1e9,le=1e9,allow_inf_nan=False)
    correlation: float = Field(ge=-1,le=1,allow_inf_nan=False)


class DeskRequest(BaseModel):
    chains: list[Selection] = Field(min_length=1,max_length=12)
    mode: Literal["session","simulation"] = "session"
    model: Literal["american","black76"] = "american"
    rate: float = Field(default=.02,ge=0,le=.5,allow_inf_nan=False)
    scenario_vol: float | None = Field(default=None,gt=0,le=5,allow_inf_nan=False)
    query: SurfacePoint | None = None
    spread: Spread | None = None


def desk_plan(root, request):
    count = sum(len(s.symbols) for s in request.chains)
    if count>96:
        raise ValueError("一次最多分析 96 份自选期权")
    contracts, subscribed = {}, {}
    for selection in request.chains:
        exchange, future = instrument("futures",selection.underlying)
        underlying = exchange+"."+future
        available = {r["symbol"]:r for r in chain(root,underlying)["contracts"]}
        for symbol in selection.symbols:
            row = available.get(symbol)
            if not row or not row.get("expiry"):
                raise ValueError("期权不属于所选标的或缺少到期日")
            expiry = int(row["expiry"])
            datetime.strptime(str(expiry),"%Y%m%d")
            contracts[symbol] = dict(row, expiry=expiry,underlying=underlying,
                                     product=exchange+"."+FUTURE.fullmatch(future)[1])
        for symbol in [underlying,*selection.symbols]:
            venue,ticker = symbol.split(".",1)
            if ticker in subscribed and subscribed[ticker]!=venue:
                raise ValueError("CTP 行情一次无法区分跨市场同名代码")
            subscribed[ticker] = venue
    if request.query:
        datetime.strptime(str(request.query.date),"%Y%m%d")
    if spread:=request.spread:
        if spread.first not in contracts or spread.second not in contracts:
            raise ValueError("请从本次订阅的期权中选择两腿波动率")
        if contracts[spread.first]["underlying"]==contracts[spread.second]["underlying"]:
            raise ValueError("Kirk 两腿需要不同的标的期货")
    return dict(contracts=list(contracts.values()),symbols=[(v,k) for k,v in subscribed.items()],
                **request.model_dump(exclude={"chains"}))
