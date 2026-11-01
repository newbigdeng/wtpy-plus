from datetime import date
import json
import pytest

from wtpy.monitor.AssetBackfill import days, normalized, save, CN
from wtpy.monitor.AssetWorkspace import asset_catalog


def test_provider_days_only():
    d=date(2025,1,2)
    with pytest.raises(ValueError):
        days(d,date(2026,1,1),CN)
    rows=[["20250101",1,2,1,2,5],["20250102",2,3,2,3,10]]
    bars=normalized(rows,d,d)
    assert [(r["date"],r["vol"]) for r in bars]==[(20250102,10)]
    with pytest.raises(ValueError,match="冲突"):
        normalized(rows+[["20250102",2,3,2,2,10]],d,d)


@pytest.mark.parametrize("asset,provider,ex,symbol",[
    ("stock","tushare","SSE","600000"),("option","edb","DCE","m2701-C-3100"),("crypto","exchange","COINBASE","BTC-USD")])
def test_native_catalog_merge(tmp_path,asset,provider,ex,symbol):
    root=tmp_path/asset
    if asset=="option":
        common=root/"common"; common.mkdir(parents=True)
        (common/"contracts.json").write_text(json.dumps({ex:{symbol:dict(product="m_o",expiredate=20261210,
            option=dict(underlying="m2701",strikeprice=3100))}}))
    bars=[dict(date=20250102,open=1,high=2,low=1,close=2,vol=9)]
    assert save(root,asset,provider,ex,symbol,bars)["added"]==1
    assert save(root,asset,provider,ex,symbol,[dict(bars[0],close=1)])["added"]==0
    item,=asset_catalog(root,asset)
    assert (item["period"],item["rows"],item["dates"])==("d",1,[20250102])
    assert not (root/"storage").exists()


def test_crypto_midnight_close(tmp_path,monkeypatch):
    from wtpy.monitor import AssetBackfill as feed
    monkeypatch.setattr(feed,"crypto_rows",lambda *args:[[202501090000,20250108,8,9,7,8,2]])
    day=date(2025,1,8)
    bars=feed.intraday(tmp_path,"crypto","binance","BINANCE","BTC-USDT",day,day,"m5")
    assert bars[0]["time"]+199000000000==202501090000
    assert feed.save(tmp_path,"crypto","binance","BINANCE","BTC-USDT",bars,"m5")["added"]==1
    item,=asset_catalog(tmp_path,"crypto")
    assert (item["code"],item["dates"],item["period"])==("BINANCE.BTC-USDT",[20250108],"m5")
