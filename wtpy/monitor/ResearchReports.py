"""Public official research catalog. One request at a time; no login or arbitrary URL fetch."""
from __future__ import annotations
from io import BytesIO
import argparse
import html
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

try:
    from .ResearchScope import classify, load_scope, priority
except ImportError:
    from ResearchScope import classify, load_scope, priority

PROVIDERS = {
    'nanhua': dict(name='南华期货', url='https://www.nanhua.net/subpage/researchIndex.html', hosts=('www.nanhua.net','mall.nanhua.net')),
    'ruida': dict(name='瑞达期货', url='https://www.rdqh.com/content/index/17', hosts=('www.rdqh.com','rdqh.com')),
    'shcifco': dict(name='上海中期', url='https://www.shcifco.com/Index/investment', hosts=('www.shcifco.com',)),
}
MAX_BYTES = 20 * 1024 * 1024
REPORT_TYPES=('周报','日报/晨会','专题/调研','其他')
TOPIC_RULES={
    '黑色':'螺纹|热卷|铁矿|焦煤|焦炭|煤焦|铁合金|双硅|黑色',
    '有色':'铜|铝|锌|铅|镍|锡|有色', '贵金属':'黄金|白银|贵金属|铂金|钯金',
    '能源化工':'原油|燃油|沥青|石油|甲醇|PTA|聚酯|乙二醇|橡胶|塑料|化工|能源',
    '农产品':'玉米|豆粕|豆油|大豆|油脂|棕榈|菜粕|棉花|白糖|生猪|鸡蛋|农产',
    '金融宏观':'股指|国债|宏观|利率|汇率|金融', '航运':'集运|航运|运价',
}


def classified(row):
    """Preserve source labels. Title-only rules are explicitly inferred search aids."""
    row=dict(row)
    source=str(row.get('source_category') or row.get('category') or '')
    title=str(row.get('title') or '')
    def kind(text):
        if re.search('周报|周度',text):return '周报'
        if re.search('日报|晨会|日度',text):return '日报/晨会'
        if re.search('专题|调研|深度',text):return '专题/调研'
        return None
    official=kind(source)
    row.update(source_category=source,report_type=official or kind(title) or '其他',
               report_type_origin='source' if official else 'title_rule',
               topic_tags=[name for name,pattern in TOPIC_RULES.items() if re.search(pattern,title,re.I)],
               tag_origin='标题规则标签，不是原文摘要')
    return row


def plain(value):
    value = re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1>', '', value)
    return html.unescape(re.sub(r'<[^>]+>', ' ', value)).strip()


def pdf_text_pages(content):
    """Extract passive text and host-derived page offsets, without OCR or file edits."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return dict(body='',text_state='unavailable',text_error='PDF全文提取器尚未安装，可直接阅读原件',source_pages=[],body_truncated=False)
    try:
        chunks=[];pages=[];cursor=0;truncated=False
        for index,page in enumerate(PdfReader(BytesIO(content)).pages):
            value=page.extract_text() or ''
            if index:chunks.append('\n');cursor+=1
            remaining=max(0,1000000-cursor);piece=value[:remaining]
            pages.append(dict(pdf_page=index+1,text_start=cursor,text_end=cursor+len(piece)))
            chunks.append(piece);cursor+=len(piece)
            if len(piece)<len(value) or cursor>=1000000:
                truncated=True;break
        text=''.join(chunks)
        return dict(body=text,text_state='extracted' if text.strip() else 'empty',text_error='',source_pages=pages,body_truncated=truncated)
    except Exception as exc:
        return dict(body='',text_state='failed',text_error=str(exc)[:500],source_pages=[],body_truncated=False)


def pdf_text(content):
    result=pdf_text_pages(content)
    return result['body'],result['text_state'],result['text_error']


class OfficialClient:
    def __init__(self, interval=2.0):
        self.interval=max(1.0,float(interval)); self.last=0

    def fetch(self, provider, url, payload=None):
        if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in PROVIDERS[provider]['hosts']:
            raise ValueError('仅访问配置的期货公司官网')
        time.sleep(max(0,self.interval-(time.monotonic()-self.last)))
        data=json.dumps(payload).encode() if payload is not None else None
        import httpx
        headers={'User-Agent':'WTResearchLibrary/1.0 (public research reader)'}
        if data is not None:headers['Content-Type']='application/json'
        self.last=time.monotonic()
        # Inspect redirects before following them, never request an unconfigured host.
        with httpx.Client(timeout=15,follow_redirects=False) as client:
            for _ in range(4):
                with client.stream('POST' if data is not None else 'GET',url,content=data,headers=headers) as response:
                    if response.is_redirect:
                        target=urljoin(url,response.headers['location'])
                        if urlsplit(target).scheme!='https' or urlsplit(target).hostname not in PROVIDERS[provider]['hosts']:
                            raise ValueError('官网跳转到未配置站点')
                        url=target;time.sleep(self.interval);continue
                    response.raise_for_status()
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>MAX_BYTES:raise ValueError('原文超过20MiB，保留官网链接')
                    return bytes(content),response.headers.get('Content-Type','')
        raise ValueError('官网跳转次数过多')

    def catalog(self, provider, limit):
        if provider=='nanhua':
            raw,_=self.fetch(provider,'https://mall.nanhua.net/mall/nh/api/report/getPage.json',
                             dict(type1Code='WEEK',pageNo=1,pageSize=limit))
            value=json.loads(raw)
            if not value.get('success'): raise ValueError('南华目录返回失败')
            output=[]
            for row in value.get('data',{}).get('result',[])[:limit]:
                identifier=str(row.get('id',''))
                title=row.get('title') or row.get('fileName')
                date=str(row.get('date') or '')[:10]
                if not identifier.isdigit() or not title or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): continue
                output.append(dict(provider_id=identifier,title=title,published_at=date,
                    source_url=row.get('detailUrl') or 'https://mall.nanhua.net/mall/nh/p/pages/nanhuaReportDetail.html?id='+identifier,
                    document_url='https://mall.nanhua.net/mall/nh/api/report/getReportFile?reportId='+identifier,
                    category=row.get('type1Name','')+' / '+row.get('type2Name',''),
                    source_category=' / '.join(str(row[key]) for key in ('type1Name','type2Name','typeName') if row.get(key))))
            return output
        raw,_=self.fetch(provider,PROVIDERS[provider]['url'])
        page=raw.decode('utf-8','replace')
        output=[]
        if provider=='ruida':
            seen=set()
            for match in re.finditer(r'(?is)<a[^>]+href=[\"\'](/content/show/(?:61|115|116|153|155)/(\d+))[\"\'][^>]*>(.*?)</a>',page):
                title=plain(match[3])
                if match[2] in seen or not title.startswith('瑞达期货') or not re.search('报告|周报|日报|晨会',title):continue
                seen.add(match[2])
                output.append(dict(provider_id=match[2],title=title,published_at='',source_url=urljoin(PROVIDERS[provider]['url'],match[1]),category='瑞达研究'))
                if len(output)>=limit:break
            if not output:raise ValueError('瑞达研究目录没有可识别研报，可能改版或限制访问')
            return output
        # Only actual research-detail links from this dedicated directory qualify.
        for match in re.finditer(r'(?is)<a[^>]+href=[\"\'](/Index/investmentinfo/id/(\d+))[\"\'][^>]*>(.*?)</a>',page):
            before=page[max(0,match.start()-250):match.start()]
            dates=re.findall(r'<span[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</span>',before)
            output.append(dict(provider_id=match[2],title=plain(match[3]),published_at=dates[-1] if dates else '',
                               source_url=urljoin(PROVIDERS[provider]['url'],match[1]),category='研究中心'))
            if len(output)>=limit:break
        if not output: raise ValueError('上海中期目录没有可识别研报，可能改版或限制访问')
        return output

    def document(self, provider, row):
        if provider=='ruida':
            content,_=self.fetch(provider,row['source_url'])
            page=content.decode('utf-8','replace')
            from pyquery import PyQuery
            document=PyQuery(page)
            title=document('.work_article h1').text()
            date=re.search(r'时间\s*[:：]\s*(\d{4}-\d{2}-\d{2})',document('.work_article').text())
            if not title or not date:raise ValueError('瑞达详情没有可识别标题或发布日期')
            row.update(title=title,published_at=date[1])
            categories=[a.text().strip() for a in document('.location dt a').items() if a.text().strip()]
            if categories:row['source_category']=' / '.join(categories)
            link=document('.load-down a').attr('href')
            if link and '.pdf' in link.lower():row['document_url']=urljoin(row['source_url'],link)
            else:
                body=document('.work_article1').text().strip()
                if len(body)<80:raise ValueError('详情无公开正文或PDF')
                return body,None
        if provider=='shcifco':
            content,_=self.fetch(provider,row['source_url'])
            page=content.decode('utf-8','replace')
            title=re.search(r'(?is)<h2[^>]*class=[\"\']boxinfo_title[\"\'][^>]*>(.*?)</h2>',page)
            date=re.search(r'发布时间\s*[:：]\s*<strong>(\d{4}-\d{2}-\d{2})</strong>',page)
            if not title or not date:raise ValueError('官网详情不可读或未识别到发布日期')
            row.update(title=plain(title[1]),published_at=date[1])
            pdf=re.search(r'(?is)href=[\"\']([^\"\']+\.pdf(?:\?[^\"\']*)?)[\"\']',page)
            if pdf:row['document_url']=urljoin(row['source_url'],html.unescape(pdf[1]))
            else:
                body=re.search(r'(?is)<div[^>]*class=[\"\']boxinfo_wen[\"\'][^>]*>(.*?)</div>',page)
                text=plain(body[1]) if body else ''
                if len(text)<80:raise ValueError('详情无公开正文或PDF')
                return text,None
        content,content_type=self.fetch(provider,row['document_url'])
        if not content.startswith(b'%PDF-'):
            raise ValueError('原文接口未返回PDF，可能需要登录或授权')
        text,state,error=pdf_text(content)
        row.update(text_state=state,text_error=error)
        return text,content


class ResearchReports:
    def __init__(self, workspace, client=None):
        self.workspace=workspace; self.client=client or OfficialClient(); self.lock=threading.Lock()

    def reports(self, query='', provider='', report_type='', topic=''):
        rows=[classified(row) for row in self.workspace._rows('official_report','__public__')]
        query=str(query).strip().casefold()
        matches=[row for row in rows if (not provider or row['provider']==provider) and
                 (not report_type or row['report_type']==report_type) and (not topic or topic in row['topic_tags']) and
                 (not query or query in ' '.join(str(row.get(key,'')) for key in ('title','organization','body','source_category','topic_tags')).casefold())]
        matches.sort(key=lambda row:(row.get('published_at') or '',row['id']),reverse=True)
        # Full text stays in SQLite and report_detail; polling sends short summaries.
        return [dict(row,body=row.get('body','')[:1600],body_truncated=len(row.get('body',''))>1600) for row in matches[:500]]

    def get(self, identifier):
        return classified(self.workspace._get('official_report','__public__',identifier))

    def filters(self):
        return dict(report_types=list(REPORT_TYPES),topics=list(TOPIC_RULES),tag_note='主题标签来自标题规则；官网分类单独保留。')

    def sources(self):
        states={row['id']:row for row in self.workspace._rows('report_source','__public__')}
        return [dict(id=key,name=value['name'],url=value['url'],interval_seconds=2,
                     **{k:v for k,v in states.get(key,{}).items() if k!='id'}) for key,value in PROVIDERS.items()]

    def collect(self, providers=None, limit=6):
        providers=providers or list(PROVIDERS)
        if any(key not in PROVIDERS for key in providers):raise ValueError('研报来源不存在')
        limit=max(1,min(20,int(limit)))
        if not self.lock.acquire(blocking=False):raise ValueError('研报采集正在运行')
        results=[]
        try:
            for provider in providers:
                state=dict(id=provider,last_attempt=time.time(),state='running',imported=0,failed=0,error='',filtered_scope=0)
                # The timer and API can run in separate processes. Claim under SQLite's writer lock.
                with self.workspace._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    previous=db.execute('SELECT data FROM records WHERE kind=? AND id=?',('report_source',provider)).fetchone()
                    throttled=previous and time.time()-json.loads(previous[0]).get('last_attempt',0)<900
                    if not throttled:
                        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',('report_source',provider,'__public__',json.dumps(state)))
                if throttled:
                    results.append(dict(provider=provider,state='throttled',message='每个来源至少间隔15分钟'));continue
                try:
                    scope=load_scope(self.workspace.root)
                    ranked=[(source,classify(source,scope)) for source in self.client.catalog(provider,limit)]
                    state['filtered_scope']=sum(decision['excluded'] for _,decision in ranked)
                    ranked=sorted((item for item in ranked if not item[1]['excluded']),key=lambda item:priority(item[1]))
                    for source,decision in ranked:
                        identifier='official-'+provider+'-'+source['provider_id']
                        try:old=self.get(identifier)
                        except ValueError:old={}
                        if old.get('fetch_state')=='excluded_scope':continue
                        if old.get('fetch_state') in ('pdf_ready','text_ready'):
                            if source.get('source_category'):old['source_category']=source['source_category']
                            path=self.workspace.root/'attachments'/(identifier+'.pdf')
                            if old.get('fetch_state')=='pdf_ready' and path.is_file():
                                text,text_state,text_error=pdf_text(path.read_bytes())
                                old.update(body=text,text_state=text_state,text_error=text_error,
                                           fetch_state='text_ready' if text else 'pdf_ready')
                            self.workspace._save('official_report','__public__',classified(old))
                            continue
                        row=dict(old,**source,id=identifier,provider=provider,organization=PROVIDERS[provider]['name'],
                                 public=True,readonly=True,body='',fetch_state='metadata_only',error='',fetched_at=None)
                        self.workspace._save('official_report','__public__',classified(row))
                        try:
                            text,pdf=self.client.document(provider,row)
                            row.update(body=text,fetch_state='text_ready' if text else 'pdf_ready',fetched_at=time.time(),
                                       text_state='extracted' if text else row.get('text_state','unavailable'))
                            row['research_scope']=classify(row,scope)
                            if row['research_scope']['excluded']:
                                row.update(body='',fetch_state='excluded_scope')
                                state['filtered_scope']+=1
                                self.workspace._save('official_report','__public__',classified(row))
                                continue
                            if pdf:
                                folder=self.workspace.root/'attachments';folder.mkdir(exist_ok=True)
                                (folder/(identifier+'.pdf')).write_bytes(pdf)
                                row.update(attachment_suffix='.pdf',attachment_name=row['title']+'.pdf',attachment_filename=row['title']+'.pdf',
                                           attachment_url='/mgr/research/attachments/'+identifier,document_bytes=len(pdf))
                            state['imported']+=1
                        except Exception as exc:
                            row.update(fetch_state='failed',error=str(exc)[:500]);state['failed']+=1
                        self.workspace._save('official_report','__public__',classified(row))
                    state.update(state='completed',last_success=time.time())
                except Exception as exc:
                    state.update(state='failed',error=str(exc)[:500])
                self.workspace._save('report_source','__public__',state);results.append(state)
            return results
        finally:self.lock.release()


def main():
    parser=argparse.ArgumentParser(description='Collect public official reports into the existing research workspace')
    parser.add_argument('--root',required=True);parser.add_argument('--provider',choices=list(PROVIDERS),action='append');parser.add_argument('--limit',type=int,default=6)
    args=parser.parse_args()
    from .ResearchWorkspace import ResearchWorkspace
    workspace=ResearchWorkspace(args.root,None)
    print(json.dumps(workspace.reports.collect(args.provider,args.limit),ensure_ascii=False))


if __name__=='__main__':main()
