"""Market display calendar. Does not change native trading/scheduling config."""
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json

CN_EXCHANGES=frozenset(("SHFE","INE","DCE","CZCE","CFFEX","GFEX"))
CALENDAR=json.loads((Path(__file__).parent/"calendars/cn-futures-2026.json").read_text(encoding="utf-8"))

def session_status(contract,sessions,holidays,now=None):
    now=(now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    identifier=contract.get("session")
    session=sessions.get(identifier)
    result={"session_open":None,"session_state":"unknown","trading_date":None,
            "trading_session":{"id":identifier,"name":session.get("name",identifier) if isinstance(session,dict) else identifier},
            "session_checked_at":int(now.timestamp()*1000),"calendar_source":"未配置"}
    if not isinstance(session,dict) or not isinstance(session.get("sections"),list):
        return result
    calendar_key=contract.get("holiday")
    configured={str(x)for x in holidays.get(calendar_key,[])if str(x).isdigit()} if isinstance(holidays,dict) else set()
    bundled=contract.get("exchange")in CN_EXCHANGES and calendar_key in ("CHINA","CN")
    def date_number(day):return int(day.strftime("%Y%m%d"))
    def covered(day):
        number=date_number(day)
        return (bundled and CALENDAR["coverage_start"]<=number<=CALENDAR["coverage_end"]) or any(x.startswith(str(day.year))for x in configured)
    def is_business(day):
        if day.weekday()>=5:return False
        number=date_number(day)
        if str(number)in configured:return False
        if bundled and any(a<=number<=b for a,b in CALENDAR["closed_ranges"]):return False
        return True
    def next_weekday(day):
        day+=timedelta(days=1)
        while day.weekday()>=5:day+=timedelta(days=1)
        return day
    minute=now.hour*100+now.minute;active=None;uncertain=False
    for section in session["sections"]:
        start,end=section.get("from"),section.get("to")
        if not isinstance(start,int) or not isinstance(end,int):continue
        cross=start>end
        inside=(minute>=start or minute<end)if cross else start<=minute<end
        if not inside:continue
        night=start>=1800 or cross
        action_day=now.date()-timedelta(days=1)if cross and minute<end else now.date()
        if not night:
            if not is_business(action_day):continue
            if not covered(action_day):uncertain=True;continue
            active=action_day;break
        trade_day=next_weekday(action_day)
        if not is_business(action_day):continue
        if date_number(action_day)in CALENDAR["no_night"]and bundled:continue
        if not is_business(trade_day):continue
        if not covered(action_day) or not covered(trade_day):uncertain=True;continue
        active=trade_day;break
    result["calendar_source"]=CALENDAR["source"]if bundled and covered(now.date())else "WT holidays.json"
    if active:
        result.update(session_open=True,session_state="open",trading_date=date_number(active))
    elif not uncertain:
        result.update(session_open=False,session_state="closed")
    return result
