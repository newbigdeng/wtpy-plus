"""Small private research collection from declared public feeds and original authors."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET

try:
    from .ResearchReports import MAX_BYTES, pdf_text
    from .ResearchScope import classify, load_scope, priority
except ImportError:
    from ResearchReports import MAX_BYTES, pdf_text
    from ResearchScope import classify, load_scope, priority


PROVIDERS = {
    'quantocracy': dict(name='Quantocracy', source_type='blog',
        url='https://feeds.feedburner.com/Quantocracy', interval=2, refresh=86400),
    'arxiv': dict(name='arXiv quantitative finance', source_type='paper',
        url='https://export.arxiv.org/api/query?' + urlencode(dict(
            search_query='(cat:q-fin.TR OR cat:q-fin.ST OR cat:q-fin.PM) AND '
                '(all:backtest OR all:overfitting OR all:"out-of-sample" OR all:factor OR all:causal)',
            sortBy='lastUpdatedDate', sortOrder='descending', max_results=12)),
        interval=3, refresh=86400),
}
# These are deliberately original-author pages, not an arbitrary URL fetch service.
BLOGS = {
    'algoadvantage.substack.com': ('.body.markup',),
    'quanterlab.com': ('article',),
    'yuratrading.com': ('article',),
    'aligrithm.com': ('article',),
    'portfoliooptimizer.io': ('section.page__content', 'article'),
    'alphaarchitect.com': ('.entry-content',),
    'qoppac.blogspot.com': ('.post-body',),
    'robotwealth.com': ('.entry-content',),
    'www.quantstart.com': ('.article-content', 'article'),
}
FEED_HOSTS = ('feeds.feedburner.com', 'quantocracy.com', 'export.arxiv.org',
              'arxiv.org', 'rss.arxiv.org')
ATOM = {'a': 'http://www.w3.org/2005/Atom'}
ARXIV_ID = re.compile(r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$')


def canonical_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('A public HTTP(S) source URL is required')
    if parsed.port not in (None, 443, 80):
        raise ValueError('Custom source ports are not supported')
    return urlunsplit(('https', parsed.hostname.lower(), parsed.path or '/', parsed.query, ''))


def feed_xml(content):
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('DTD/entity declarations are not accepted in source feeds')
    return ET.fromstring(content)


def pdf_document(content):
    """Use the existing PDF reader, retaining exact page offsets for source anchors."""
    try:
        from pypdf import PdfReader
    except ImportError:
        text, state, error = pdf_text(content)
        return dict(body=text, text_state=state, text_error=error, source_pages=[])
    try:
        pieces, pages, offset = [], [], 0
        for number, page in enumerate(PdfReader(BytesIO(content)).pages, 1):
            text = page.extract_text() or ''
            pieces.append(text)
            pages.append(dict(pdf_page=number, text_start=offset, text_end=offset + len(text)))
            offset += len(text) + 1
            if offset > 1000000:
                raise ValueError('PDF full text exceeds the existing 1M-character limit')
        body = '\n'.join(pieces)
        return dict(body=body, text_state='extracted' if body.strip() else 'empty', text_error='', source_pages=pages)
    except Exception as error:
        return dict(body='', text_state='failed', text_error=str(error)[:500], source_pages=[])


def atom_catalog(content):
    result = []
    for entry in feed_xml(content).findall('a:entry', ATOM):
        identifier = entry.findtext('a:id', '', ATOM).split('/abs/')[-1]
        if not ARXIV_ID.fullmatch(identifier):
            continue
        title = ' '.join(entry.findtext('a:title', '', ATOM).split())
        published = entry.findtext('a:published', '', ATOM)
        updated = entry.findtext('a:updated', '', ATOM)
        result.append(dict(provider_id=identifier, title=title,
            source_url='https://arxiv.org/abs/' + identifier,
            canonical_url='https://arxiv.org/abs/' + identifier,
            fulltext_url='https://arxiv.org/html/' + identifier,
            pdf_url='https://arxiv.org/pdf/' + identifier,
            source_version=identifier.rsplit('v', 1)[-1],
            authors=[a.findtext('a:name', '', ATOM) for a in entry.findall('a:author', ATOM)],
            source_published_at=published, source_updated_at=updated,
            published_at=published[:10],
            publication_time_semantics='arxiv_first_submission_processed',
            abstract=entry.findtext('a:summary', '', ATOM).strip(),
            source_category=' / '.join(c.get('term', '') for c in entry.findall('a:category', ATOM))))
    return result


def quantocracy_catalog(content):
    from pyquery import PyQuery
    result, seen = [], set()
    for item in feed_xml(content).findall('./channel/item'):
        index_url = item.findtext('link', '')
        indexed = item.findtext('pubDate', '')
        try:
            indexed = parsedate_to_datetime(indexed).isoformat()
        except (TypeError, ValueError):
            indexed = ''
        body = next((x.text or '' for x in item if x.tag.endswith('}encoded')), '')
        # RSS descriptions are digest excerpts too; neither field is original full text.
        body = body or item.findtext('description', '')
        for anchor in PyQuery(body)('a[href]').items():
            try:
                url = canonical_url(anchor.attr('href'))
            except ValueError:
                continue
            host = urlsplit(url).hostname
            title = anchor.text().strip()
            if host in ('quantocracy.com', 'www.quantocracy.com', 'feeds.feedburner.com') or len(title) < 12 or url in seen:
                continue
            seen.add(url)
            result.append(dict(title=title, source_url=url, canonical_url=url,
                index_url=index_url, indexed_at=indexed, authors=[], published_at='',
                source_published_at='', source_updated_at='', source_version='',
                publication_time_semantics='unknown_original_publication', source_category='Quantocracy digest link'))
    return sorted(result, key=lambda row: (
        not bool(re.search(r'overfit|backtest|causal|out.of.sample|validation|leakage|regression', row['title'], re.I)),
        urlsplit(row['source_url']).hostname not in BLOGS))


class PublicSourceClient:
    def __init__(self, proxy=None, scope=None):
        self.last_request = {}
        self.proxy = proxy
        self.scope = scope or {}

    def catalog_url(self, provider):
        url = PROVIDERS[provider]['url']
        if provider == 'arxiv' and self.scope.get('enabled'):
            query = '(cat:q-fin.TR OR cat:q-fin.ST OR cat:q-fin.PM) AND (' + ' OR '.join(
                'all:"' + term + '"' for term in ('intraday', 'high frequency', 'short term', 'short horizon',
                'next day', 'one day', 'microstructure', 'order flow', 'backtest overfitting',
                'deflated Sharpe', 'transaction costs', 'data leakage', 'factor decay')) + ')'
            url = 'https://export.arxiv.org/api/query?' + urlencode(dict(search_query=query,
                sortBy='lastUpdatedDate', sortOrder='descending', max_results=12))
        return url

    def fetch(self, url, *, hosts, interval=3):
        """Bounded static bytes; redirects cannot introduce another host or TLS downgrade."""
        import httpx
        current = canonical_url(url)
        with httpx.Client(timeout=20, follow_redirects=False, proxy=self.proxy,
                          headers={'User-Agent': 'WTResearchLibrary/1.0 (private research reader)'}) as client:
            for _ in range(4):
                host = urlsplit(current).hostname
                if host not in hosts:
                    raise ValueError('Source host is not configured for this fetch')
                group = 'arxiv' if host.endswith('arxiv.org') else host
                time.sleep(max(0, interval - (time.monotonic() - self.last_request.get(group, 0))))
                self.last_request[group] = time.monotonic()
                with client.stream('GET', current) as response:
                    if response.is_redirect:
                        target = urljoin(current, response.headers['location'])
                        if urlsplit(target).scheme != 'https':
                            raise ValueError('Source attempted an HTTPS downgrade')
                        current = canonical_url(target)
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise ValueError('Source exceeds the existing 20MiB limit')
                    return bytes(content), response.headers.get('Content-Type', '')
        raise ValueError('Too many source redirects')

    def catalog(self, provider):
        settings = PROVIDERS[provider]
        content, _ = self.fetch(self.catalog_url(provider), hosts=FEED_HOSTS, interval=settings['interval'])
        return atom_catalog(content) if provider == 'arxiv' else quantocracy_catalog(content)

    def document(self, provider, row):
        from pyquery import PyQuery
        host = urlsplit(row['source_url']).hostname
        if provider == 'arxiv':
            html_error = None
            try:
                raw, _ = self.fetch(row['fulltext_url'], hosts=('arxiv.org',), interval=3)
                document = PyQuery(raw.decode('utf-8', 'replace'))
                article = document('article.ltx_document')
                article('script,style,nav,form').remove()
                text = article.text().strip()
                if len(text) < 200:
                    raise ValueError('No recognizable complete arXiv HTML article')
                return dict(body=text, body_scope='full_text', text_state='extracted', fulltext_format='html'), None
            except Exception as error:
                html_error = str(error)[:300]
            raw, _ = self.fetch(row['pdf_url'], hosts=('arxiv.org',), interval=3)
            if not raw.startswith(b'%PDF-'):
                raise ValueError('arXiv PDF endpoint returned no PDF')
            extracted = pdf_document(raw)
            return dict(extracted, body_scope='full_text' if extracted['body'].strip() else 'none',
                        html_retry_error=html_error, fulltext_format='pdf', fulltext_url=row['pdf_url']), raw
        if host not in BLOGS:
            return dict(body='', body_scope='none', text_state='unsupported_source',
                text_error='Original author host is not enabled; digest is only an index'), None
        raw, _ = self.fetch(row['source_url'], hosts=(host,), interval=2)
        document = PyQuery(raw.decode('utf-8', 'replace'))
        article = next((document(selector) for selector in BLOGS[host] if document(selector)), None)
        if article is None:
            raise ValueError('Original article selector is missing; do not use the whole page')
        if document('[class*="paywall"], [class*="subscription-required"], [data-testid="paywall"]').length:
            raise ValueError('Original article requires access; no paywall bypass')
        article('script,style,nav,form').remove()
        text = article.text().strip()
        if len(text) < 200:
            raise ValueError('Original article is empty or incomplete')
        authors = [a.attr('content') for a in document('meta[name="author"]').items() if a.attr('content')]
        published = document('meta[property="article:published_time"]').attr('content') or ''
        updated = document('meta[property="article:modified_time"]').attr('content') or ''
        canonical = document('link[rel="canonical"]').attr('href')
        canonical = canonical_url(urljoin(row['source_url'], canonical)) if canonical else row['source_url']
        if urlsplit(canonical).hostname != host:
            canonical = row['source_url']
        return dict(body=text, body_scope='full_text', text_state='extracted', fulltext_format='html', fulltext_url=row['source_url'],
            source_url=canonical, canonical_url=canonical,
            authors=authors, source_published_at=published, source_updated_at=updated,
            published_at=published[:10] if published else '',
            publication_time_semantics='author_metadata' if published else 'unknown_original_publication'), None


class PublicResearchSources:
    def __init__(self, workspace, owner, client=None):
        self.workspace = workspace
        self.owner = owner
        self.client = client or PublicSourceClient(scope=load_scope(workspace.root))
        if not owner or owner == '__public__':
            raise ValueError('Full-text collection requires a private research owner')

    def sources(self):
        states = {row['provider']: row for row in self.workspace._rows('research_source', self.owner)}
        return [dict(id=key, **settings, state=states.get(key, {})) for key, settings in PROVIDERS.items()]

    def collect(self, providers=None, limit=6):
        providers = list(PROVIDERS) if providers is None else providers
        if any(p not in PROVIDERS for p in providers):
            raise ValueError('Unknown research source')
        limit = max(1, min(12, int(limit)))
        output = []
        for provider in providers:
            now = time.time()
            states = self.workspace._rows('research_source', self.owner)
            state = next((x for x in states if x['provider'] == provider),
                         dict(id=uuid.uuid4().hex, provider=provider, imported=0, failed=0))
            records = [x for x in self.workspace._rows('report', self.owner) if x.get('provider') == provider]
            scope = load_scope(self.workspace.root)
            retry = [x for x in records if x.get('fetch_state') not in ('text_ready', 'excluded_scope')
                     and (x.get('text_state') != 'unsupported_source' or urlsplit(x['source_url']).hostname in BLOGS)
                     and x.get('retry_after', 0) <= now and not classify(x, scope)['excluded']]
            catalog_failed = state.get('catalog_failed', bool(state.get('error')) or state.get('state') == 'failed')
            if state.get('next_attempt', 0) > now and not retry:
                output.append(dict(provider=provider, state='failed' if catalog_failed else state.get('state') if state.get('state') in
                    ('failed', 'completed_with_errors') else 'cached', error=state.get('error', '')))
                continue
            catalog_due = state.get('next_attempt', 0) <= now
            state.update(state='running', last_attempt=now, imported=0, failed=0, filtered_scope=0)
            self.workspace._save('research_source', self.owner, state)
            try:
                # Reuse a cached catalog while retrying failed full text; no repeated feed poll.
                sources = self.client.catalog(provider) if catalog_due else retry
                if catalog_due:
                    catalog_failed = False
                    state.update(error='', next_attempt=time.time() + PROVIDERS[provider]['refresh'])
                existing = {url: row for row in records for url in
                            (row['canonical_url'], row.get('discovery_url', row['canonical_url']))}
                ranked = [(source, classify(source, scope)) for source in sources]
                state['filtered_scope'] = sum(decision['excluded'] for _, decision in ranked)
                ranked = sorted((item for item in ranked if not item[1]['excluded']), key=lambda item: priority(item[1]))
                for source, decision in ranked[:limit]:
                    old = existing.get(source['canonical_url'], {})
                    if old.get('fetch_state') in ('text_ready', 'excluded_scope') or (
                        old.get('text_state') == 'unsupported_source' and urlsplit(source['source_url']).hostname not in BLOGS):
                        continue
                    row = dict(old, **source)
                    row.update(id=old.get('id') or 'research-' + provider + '-' + uuid.uuid4().hex,
                        provider=provider, source_type=PROVIDERS[provider]['source_type'],
                        public=False, readonly=True, owner=self.owner,
                        organization=PROVIDERS[provider]['name'], body='', body_scope='none', fetch_state='metadata_only',
                        availability_at=None, availability_basis='No point-in-time input-data evidence',
                        discovered_at=old.get('discovered_at', now), fetched_at=None,
                        discovery_url=old.get('discovery_url', source['canonical_url']),
                        index_url=source.get('index_url', PROVIDERS[provider]['url']))
                    try:
                        extracted, pdf = self.client.document(provider, row)
                        row.update(extracted, fetched_at=time.time(), error='')
                        row['research_scope'] = classify(row, scope)
                        if row['research_scope']['excluded']:
                            row.update(body='', body_scope='none', fetch_state='excluded_scope')
                            state['filtered_scope'] += 1
                            self.workspace._save('report', self.owner, row)
                            continue
                        row['fetch_state'] = 'text_ready' if row.get('body', '').strip() else 'pdf_ready' if pdf else 'unsupported_source' if row.get('text_state') == 'unsupported_source' else 'metadata_only'
                        if len(row.get('body', '')) >= 1000000:
                            row.update(body='', body_scope='none', text_state='failed', fetch_state='failed', error='Extracted text exceeds 1M characters')
                        if pdf:
                            folder = self.workspace.root / 'attachments'
                            folder.mkdir(exist_ok=True)
                            path = folder / (row['id'] + '.pdf')
                            path.write_bytes(pdf)
                            path.chmod(0o600)
                            row.update(attachment_suffix='.pdf', attachment_name=row['title'] + '.pdf', document_bytes=len(pdf))
                        if row['fetch_state'] == 'text_ready':
                            state['imported'] += 1
                        else:
                            row['retry_after'] = time.time() + 86400
                    except Exception as error:
                        row.update(fetch_state='failed', text_state='failed', error=str(error)[:500],
                                   retry_after=time.time() + 3600)
                        state['failed'] += 1
                    self.workspace._save('report', self.owner, row)
                state.update(state='completed_with_errors' if state['failed'] or catalog_failed else 'completed',
                             catalog_failed=catalog_failed)
                if not catalog_failed and not state['failed']:
                    state.update(last_success=time.time(), error='')
            except Exception as error:
                state.update(state='failed', catalog_failed=True, error=str(error)[:500], next_attempt=time.time() + 3600)
            self.workspace._save('research_source', self.owner, state)
            output.append(state)
        return output
