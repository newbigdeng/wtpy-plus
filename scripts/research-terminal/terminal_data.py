"""On-demand public data with bounded requests, provenance and stale-cache states."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler


SOURCES = [
    dict(id='worldbank',name='世界银行',access='免密钥',url='https://datahelpdesk.worldbank.org/knowledgebase/topics/125589',note='宏观背景；年度数据不能当作日内信号'),
    dict(id='dbnomics',name='DBnomics',access='免密钥',url='https://docs.db.nomics.world/web-api/',note='汇集官方序列；修订历史不等于历史时点数据'),
    dict(id='imf',name='IMF · 经 DBnomics',access='免密钥',url='https://db.nomics.world/IMF',note='显示 IMF 原始来源和中转服务'),
    dict(id='ecb',name='欧洲央行',access='免密钥',url='https://data.ecb.europa.eu/help/api/overview',note='官方汇率日频参考价'),
    dict(id='akshare',name='AkShare',access='免密钥',url='https://akshare.akfamily.xyz/',note='公开行情采集；供应方限流时保留缓存'),
    dict(id='kraken',name='Kraken',access='公开行情免密钥',url='https://docs.kraken.com/api-reference/market-data/get-ticker-information',note='仅行情，不连接加密货币交易权限'),
    dict(id='yahoo',name='Yahoo Finance',access='公开接口，非官方保证',url='https://finance.yahoo.com/',note='可能限流；个人研究，不作 WT 执行报价'),
    dict(id='fred',name='FRED',access='需申请免费密钥',url='https://fred.stlouisfed.org/docs/api/api_key.html',note='未配置时可使用 DBnomics 的 FRED 公共序列',key='FRED_API_KEY'),
    dict(id='polygon',name='Polygon / Massive',access='免费层需密钥，暂不启用',url='https://massive.com/pricing',note='免费额度和再分发限制需按账户确认',disabled=True),
    dict(id='gdelt',name='GDELT',access='免密钥',url='https://gdeltproject.org/',note='新闻索引，不代表事实已被独立证实'),
    dict(id='portwatch',name='IMF PortWatch',access='公开航运数据',url='https://portwatch.imf.org/',note='航道基础信息；不是实时 AIS 船位'),
    dict(id='ais',name='AIS Stream',access='需申请免费密钥',url='https://www.aisstream.io/documentation',note='当前未接入实时船位，不以新闻推算船舶位置',disabled=True),
]

DATASETS = [
    dict(id='cn_gdp',source='worldbank',name='中国 GDP 增速',frequency='年度',unit='%',query='CHN/NY.GDP.MKTP.KD.ZG'),
    dict(id='cn_cpi',source='worldbank',name='中国 CPI 年度涨幅',frequency='年度',unit='%',query='CHN/FP.CPI.TOTL.ZG'),
    dict(id='us_rate',source='dbnomics',name='美国有效联邦基金利率',frequency='月度',unit='%',query='FED/H15/RIFSPFF_N.M'),
    dict(id='imf_growth',source='imf',name='IMF 中国实际 GDP 增速（含预测）',frequency='年度',unit='%',query='IMF/WEO:latest/CHN.NGDP_RPCH'),
    dict(id='eur_usd',source='ecb',name='EUR / USD 参考汇率',frequency='日度',unit='USD/EUR',query='D.USD.EUR.SP00.A'),
    dict(id='rb_spot',source='akshare',name='螺纹钢公开行情',frequency='快照',unit='CNY',query='RB2701'),
    dict(id='ta_spot',source='akshare',name='PTA 公开行情',frequency='快照',unit='CNY',query='TA701'),
    dict(id='btc',source='kraken',name='BTC / USD',frequency='快照',unit='USD',query='XBTUSD'),
    dict(id='eth',source='kraken',name='ETH / USD',frequency='快照',unit='USD',query='ETHUSD'),
    dict(id='sp500',source='yahoo',name='标普 500',frequency='日度',unit='指数',query='^GSPC'),
    dict(id='wti',source='yahoo',name='WTI 近月参考价',frequency='日度',unit='USD/桶',query='CL=F'),
    dict(id='fred_rate',source='fred',name='FRED 联邦基金利率',frequency='月度',unit='%',query='FEDFUNDS'),
    dict(id='shipping_news',source='gdelt',name='航运与能源新闻',frequency='新闻索引',unit='',query='(shipping OR "Red Sea" OR "Strait of Hormuz")'),
    dict(id='chokepoints',source='portwatch',name='全球主要航道',frequency='静态地理资料',unit='',query='fa9a5800b0ee4855af8b2944ab1e07af'),
]


def fetch_json(url):
    return json.loads(fetch_text(url))


def fetch_text(url):
    request=Request(url,headers={'User-Agent':'WT-Research/1.0 (personal analytics)','Accept':'application/json,text/csv,*/*'})
    # HTTPS certificates always verified. Environment proxy is host managed.
    with build_opener(ProxyHandler()).open(request,timeout=18) as response:
        payload=response.read(4*1024*1024+1)
        if len(payload)>4*1024*1024:raise ValueError('来源响应超过 4 MiB')
        return payload.decode('utf-8-sig')


def safe_url(value):
    return value if urlsplit(str(value)).scheme in ('http','https') else ''


def collect(item):
    source=item['source'];query=item['query'];series=[];rows=[];links=[]
    if source=='worldbank':
        country,indicator=query.split('/')
        url=f'https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json&per_page=60'
        data=fetch_json(url)
        if not isinstance(data,list) or len(data)<2:raise ValueError('世界银行没有返回该指标')
        series=sorted([dict(time=r['date'],value=r['value']) for r in (data[1] or []) if r['value'] is not None],key=lambda r:r['time'])
    elif source in ('dbnomics','imf'):
        url='https://api.db.nomics.world/v22/series/'+query+'?observations=1'
        data=fetch_json(url)['series']['docs'][0]
        links.append(dict(label='实际数据集版本',value=data.get('dataset_code'),source_updated_at=data.get('indexed_at')))
        series=[dict(time=str(t),value=float(v)) for t,v in zip(data['period'],data['value']) if isinstance(v,(int,float))][-300:]
    elif source=='ecb':
        url='https://data-api.ecb.europa.eu/service/data/EXR/'+query+'?format=csvdata&lastNObservations=180'
        series=[dict(time=r['TIME_PERIOD'],value=float(r['OBS_VALUE'])) for r in csv.DictReader(io.StringIO(fetch_text(url))) if r.get('OBS_VALUE')]
    elif source=='kraken':
        url='https://api.kraken.com/0/public/Ticker?pair='+query;data=fetch_json(url)
        if data.get('error'):raise ValueError('Kraken 行情暂不可用')
        row=next(iter(data['result'].values()))
        rows=[dict(symbol=query,last=float(row['c'][0]),bid=float(row['b'][0]),ask=float(row['a'][0]),volume_24h=float(row['v'][1]))]
    elif source=='yahoo':
        url='https://query1.finance.yahoo.com/v8/finance/chart/'+query+'?range=6mo&interval=1d'
        data=fetch_json(url)['chart']['result'][0]
        series=[dict(time=time.strftime('%Y-%m-%d',time.gmtime(t)),value=float(v)) for t,v in zip(data['timestamp'],data['indicators']['quote'][0]['close']) if v is not None]
    elif source=='akshare':
        url='https://akshare.akfamily.xyz/data/futures/futures.html'
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),query],capture_output=True,text=True,timeout=25)
        if result.returncode:raise ValueError('AkShare 上游行情暂不可用')
        rows=json.loads(result.stdout.splitlines()[-1])
    elif source=='fred':
        key=os.environ.get('FRED_API_KEY')
        if not key:raise ValueError('需要在服务器配置 FRED 免费密钥')
        base='https://api.stlouisfed.org/fred/series/observations'
        data=fetch_json(base+'?'+urlencode(dict(series_id=query,api_key=key,file_type='json',sort_order='desc',limit=180)))
        url='https://fred.stlouisfed.org/series/'+query
        series=list(reversed([dict(time=r['date'],value=float(r['value'])) for r in data['observations'] if r['value']!='.']))
    elif source=='gdelt':
        url='https://api.gdeltproject.org/api/v2/doc/doc?'+urlencode(dict(query=query,mode='artlist',format='json',maxrecords=40,sort='datedesc',timespan='3d'))
        data=fetch_json(url)
        rows=[dict(title=r['title'],url=safe_url(r['url']),time=r.get('seendate'),country=r.get('sourcecountry'),domain=r.get('domain')) for r in data.get('articles',[])]
    elif source=='portwatch':
        info=fetch_json('https://www.arcgis.com/sharing/rest/content/items/'+query+'?f=json')
        service=info.get('url','')
        if urlsplit(service).hostname not in ('services9.arcgis.com','services.arcgis.com','services7.arcgis.com'):
            raise ValueError('PortWatch 数据地址待核对')
        url=service.rstrip('/')+'/0/query?'+urlencode(dict(where='1=1',outFields='*',f='json',resultRecordCount=40))
        result=fetch_json(url)
        if result.get('error'):raise ValueError('PortWatch 暂未提供可读航道资料')
        rows=[dict(**r['attributes'],longitude=r.get('geometry',{}).get('x'),latitude=r.get('geometry',{}).get('y')) for r in result.get('features',[])]
    else:raise ValueError('该来源尚未启用')
    if not series and not rows and source!='gdelt':raise ValueError('来源本次未返回数据')
    return dict(series=series,rows=rows,source_url=url,links=links)


class DataHub:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock()

    def catalog(self):
        sources=[dict(s,configured=not s.get('disabled') and (not s.get('key') or bool(os.environ.get(s['key'])))) for s in SOURCES]
        return dict(sources=sources,datasets=[{k:v for k,v in d.items() if k!='query'} for d in DATASETS])

    def read(self,identifier,refresh=False):
        item=next((x for x in DATASETS if x['id']==identifier),None)
        if not item:raise ValueError('未知数据集')
        path=self.root/(identifier+'.json')
        with self.lock:
            cached=json.loads(path.read_text()) if path.exists() else None
            ttl=60 if item['frequency']=='快照' else 900 if item['source']=='gdelt' else 21600
            age=time.time()-(cached or {}).get('fetched_at',0)
            if cached and (not refresh or age<ttl):
                return dict(cached,cached=True,stale=age>=ttl)
            if not refresh:return dict(dataset=identifier,status='not_loaded',series=[],rows=[],note='管理员加载后，游客可查看缓存')
            try:
                data=collect(item)
                value=dict(dataset=identifier,source=item['source'],title=item['name'],unit=item['unit'],frequency=item['frequency'],
                    status='ready',fetched_at=time.time(),pit_ready=False,
                    note='抓取时刻不等于历史公布时刻。未核定公布时间的数据只作研究背景。',**data)
                tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False));tmp.replace(path)
                return dict(value,cached=False,stale=False)
            except (OSError,ValueError,KeyError,IndexError,TypeError,subprocess.TimeoutExpired):
                # Do not echo provider URLs: some carry credentials.
                if cached:return dict(cached,cached=True,stale=True,error='来源暂时不可用，保留上次数据')
                return dict(dataset=identifier,status='unavailable',series=[],rows=[],error='来源暂时不可用或尚未配置，请稍后再试')


if __name__=='__main__':
    import akshare as ak
    frame=ak.futures_zh_spot(symbol=sys.argv[1],market='CF',adjust='0')
    print(frame.to_json(orient='records',force_ascii=False))
