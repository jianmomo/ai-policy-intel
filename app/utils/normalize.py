import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from app.schemas import CollectedItem

SECURE_DOMAINS = {
    'gov.cn',
    'www.gov.cn',
    'miit.gov.cn',
    'www.miit.gov.cn',
    'ndrc.gov.cn',
    'www.ndrc.gov.cn',
    'cac.gov.cn',
    'www.cac.gov.cn',
}


def clean_text(value: str) -> str:
    stripped = re.sub(r"\s+", " ", value or "")
    return stripped.strip()


def normalize_url(url: str) -> str:
    parts = urlsplit((url or '').strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == 'http' and netloc in SECURE_DOMAINS:
        scheme = 'https'
    normalized = urlunsplit((scheme, netloc, parts.path, parts.query, ''))
    return normalized.rstrip('/')


def normalize_item(item: CollectedItem) -> CollectedItem:
    item.title = clean_text(item.title)
    item.url = normalize_url(item.url)
    item.raw_summary = clean_text(item.raw_summary)
    item.raw_content = clean_text(item.raw_content)
    return item


def item_hash(title: str, url: str) -> str:
    return hashlib.sha256(f"{clean_text(title).lower()}::{normalize_url(url)}".encode('utf-8')).hexdigest()
