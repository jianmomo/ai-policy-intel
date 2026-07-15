import html
import re
import smtplib
from collections import Counter
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import httpx
from deep_translator import GoogleTranslator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Item, Source
from app.topic_intel import topic_name_for_item
from app.utils.dedup import items_look_duplicate

OFFICIAL_POLICY_SOURCE_IDS = {"gov-policy", "miit-policy", "ndrc-notice"}
BROAD_POLICY_SOURCE_IDS = {"gov-news", "miit", "cac-gov"}


def email_delivery_configured() -> bool:
    return settings.delivery_email_enabled and all([settings.smtp_host, settings.smtp_from, settings.smtp_to])


def telegram_delivery_configured() -> bool:
    has_chat = any(
        [
            settings.telegram_chat_id,
            settings.telegram_ai_chat_id,
            settings.telegram_policy_chat_id,
        ]
    )
    return settings.delivery_telegram_enabled and bool(settings.telegram_bot_token) and has_chat


def ops_telegram_configured() -> bool:
    return settings.delivery_health_alerts_enabled and bool(settings.telegram_bot_token) and bool(settings.telegram_ops_chat_id or settings.telegram_chat_id)


def build_email_notice(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8")


def _clean_digest_text(value: str) -> str:
    normalized = re.sub(r"<[^>]+>", " ", value or "")
    normalized = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", normalized)
    normalized = html.unescape(normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_source_meta(value: str) -> dict[str, str]:
    parts = [part.strip() for part in (value or "").split("|") if part.strip()]
    if not parts:
        return {}
    meta = {"source": parts[0]}
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, raw = part.split(":", 1)
        meta[key.strip().lower()] = raw.strip()
    return meta


def _parse_digest_markdown(markdown: str) -> dict[str, object]:
    digest: dict[str, object] = {"title": "", "meta": {}, "sections": []}
    meta: dict[str, str] = digest["meta"]
    sections: list[dict[str, object]] = digest["sections"]
    current_section: dict[str, object] | None = None
    current_item: dict[str, object] | None = None

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("# "):
            digest["title"] = line[2:].strip()
            continue
        if line.startswith("## "):
            if current_item and current_section is not None:
                current_section["items"].append(current_item)
                current_item = None
            if current_section is not None:
                sections.append(current_section)
            current_section = {"name": line[3:].strip(), "items": []}
            continue
        if current_section is None and line.startswith("- ") and ":" in line[2:]:
            key, raw = line[2:].split(":", 1)
            meta[key.strip()] = raw.strip()
            continue
        if line.startswith("- ["):
            if current_section is None:
                continue
            if current_item is not None:
                current_section["items"].append(current_item)
            match = re.match(r"^- \[(?P<title>.+?)\]\((?P<url>.+?)\)$", line)
            if not match:
                current_item = {
                    "title": _clean_digest_text(line[2:]),
                    "url": "",
                    "details": {},
                    "extras": [],
                }
                continue
            current_item = {
                "title": match.group("title").strip(),
                "url": match.group("url").strip(),
                "details": {},
                "extras": [],
            }
            continue
        if current_item is not None and line.startswith("  "):
            detail = stripped
            if ":" in detail:
                key, raw = detail.split(":", 1)
                current_item["details"][key.strip()] = raw.strip()
            else:
                current_item["extras"].append(detail)

    if current_item and current_section is not None:
        current_section["items"].append(current_item)
    if current_section is not None:
        sections.append(current_section)
    return digest


def _section_anchor(name: str, index: int) -> str:
    normalized = _normalized_title(name).replace(" ", "-")
    return normalized or f"section-{index}"


def _render_email_html(digest: dict[str, object], subject: str) -> str:
    title = str(digest.get("title") or subject or settings.app_name)
    meta = digest.get("meta") or {}
    sections = digest.get("sections") or []
    raw_markdown = str(digest.get("_raw_markdown") or "")
    generated_at = str(meta.get("Generated at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    parsed_item_count = sum(len(section.get("items", [])) for section in sections)
    declared_items = str(meta.get("Items") or parsed_item_count)
    preheader = f"{title} | {generated_at} | {declared_items} \u6761\u91cd\u70b9\u4fe1\u606f"

    parts = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{_escape(title)}</title>",
        "<style>",
        "body{margin:0;padding:0;background:#f3f6fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;}",
        ".preheader{display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;}",
        ".wrap{max-width:780px;margin:0 auto;padding:20px 12px 32px;}",
        ".hero{background:linear-gradient(135deg,#0f172a,#1d4ed8);color:#fff;border-radius:20px;padding:24px 22px;box-shadow:0 16px 40px rgba(15,23,42,.18);}",
        ".hero h1{margin:0 0 10px;font-size:28px;line-height:1.2;}",
        ".hero p{margin:0;font-size:14px;line-height:1.7;color:rgba(255,255,255,.88);}",
        ".meta-row{margin-top:16px;}",
        ".pill{display:inline-block;margin:0 8px 8px 0;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.14);font-size:12px;color:#fff;}",
        ".panel{background:#fff;border-radius:18px;padding:18px 18px 10px;margin-top:16px;box-shadow:0 10px 30px rgba(15,23,42,.08);}",
        ".panel h2{margin:0 0 12px;font-size:18px;color:#0f172a;}",
        ".nav-grid{font-size:0;}",
        ".nav-card{display:inline-block;vertical-align:top;width:calc(50% - 8px);margin:0 8px 10px 0;background:#f8fafc;border:1px solid #dbe4f0;border-radius:14px;padding:12px 14px;box-sizing:border-box;}",
        ".nav-name{font-size:14px;font-weight:700;color:#0f172a;margin:0 0 6px;}",
        ".nav-count{font-size:12px;color:#475569;}",
        ".section-title{margin:26px 0 12px;font-size:20px;color:#0f172a;}",
        ".section-sub{margin:0 0 14px;font-size:13px;color:#64748b;}",
        ".item-card{background:#fff;border-radius:18px;padding:18px;margin:0 0 14px;box-shadow:0 8px 26px rgba(15,23,42,.08);border:1px solid #e7eef7;}",
        ".item-title{margin:0 0 12px;font-size:18px;line-height:1.45;color:#0f172a;}",
        ".item-title a{color:#0f172a;text-decoration:none;}",
        ".item-title a:hover{text-decoration:underline;}",
        ".chips{margin:0 0 10px;}",
        ".chip{display:inline-block;margin:0 8px 8px 0;padding:5px 10px;border-radius:999px;background:#eef4ff;color:#1d4ed8;font-size:12px;font-weight:600;}",
        ".label{font-size:12px;font-weight:700;letter-spacing:.04em;color:#64748b;text-transform:uppercase;margin:12px 0 6px;}",
        ".text{font-size:14px;line-height:1.75;color:#334155;margin:0;}",
        ".button{display:inline-block;margin-top:14px;padding:10px 14px;border-radius:12px;background:#1d4ed8;color:#fff!important;text-decoration:none;font-size:13px;font-weight:700;}",
        ".footer{margin-top:18px;font-size:12px;line-height:1.7;color:#64748b;text-align:center;}",
        "@media only screen and (max-width:640px){.wrap{padding:12px 8px 24px;}.hero{padding:20px 16px;border-radius:16px;}.hero h1{font-size:24px;}.panel,.item-card{padding:16px;border-radius:16px;}.nav-card{display:block;width:100%;margin-right:0;}}",
        "</style>",
        "</head>",
        "<body>",
        f'<div class="preheader">{_escape(preheader)}</div>',
        '<div class="wrap">',
        '<div class="hero">',
        f"<h1>{_escape(title)}</h1>",
        "<p>\u8fd9\u5c01\u90ae\u4ef6\u4fdd\u7559\u4e86\u539f\u59cb\u8bc4\u5206\u3001\u547d\u4e2d\u539f\u56e0\u3001\u6807\u7b7e\u548c\u6458\u8981\uff0c\u9002\u5408\u4f60\u5feb\u901f\u626b\u4e00\u904d\uff0c\u518d\u6309\u9700\u6df1\u5165\u67e5\u770b\u539f\u6587\u3002</p>",
        '<div class="meta-row">',
        f'<span class="pill">\u751f\u6210\u65f6\u95f4\uff1a{_escape(generated_at)}</span>',
        f'<span class="pill">\u6761\u76ee\u6570\uff1a{_escape(declared_items)}</span>',
        f'<span class="pill">\u5206\u533a\u6570\uff1a{len(sections)}</span>',
        "</div>",
        "</div>",
    ]

    if sections:
        parts.extend([
            '<div class="panel">',
            '<h2>\u5feb\u901f\u5bfc\u822a</h2>',
            '<div class="nav-grid">',
        ])
        for index, section in enumerate(sections, start=1):
            name = str(section.get("name") or f"Section {index}")
            anchor = _section_anchor(name, index)
            count = len(section.get("items", []))
            parts.append(
                f'<a class="nav-card" href="#{_escape(anchor)}" style="text-decoration:none;">'
                f'<div class="nav-name">{_escape(name)}</div>'
                f'<div class="nav-count">{count} \u6761\u4fe1\u606f</div>'
                '</a>'
            )
        parts.extend(["</div>", "</div>"])

    for section_index, section in enumerate(sections, start=1):
        name = str(section.get("name") or f"Section {section_index}")
        anchor = _section_anchor(name, section_index)
        items = section.get("items", [])
        parts.append(f'<h2 class="section-title" id="{_escape(anchor)}">{_escape(name)}</h2>')
        parts.append(f'<p class="section-sub">\u672c\u5206\u533a\u5171 {len(items)} \u6761\uff0c\u6309\u539f\u59cb digest \u6392\u5e8f\u5c55\u793a\u3002</p>')
        for item_index, item in enumerate(items, start=1):
            details = item.get("details", {})
            source_meta = _parse_source_meta(str(details.get("Source", "")))
            chips: list[str] = []
            if source_meta.get("source"):
                chips.append(f'\u6765\u6e90\uff1a{_escape(source_meta["source"])}')
            if source_meta.get("published"):
                chips.append(f'\u53d1\u5e03\u65f6\u95f4\uff1a{_escape(source_meta["published"])}')
            if source_meta.get("score"):
                chips.append(f'\u8bc4\u5206\uff1a{_escape(source_meta["score"])}')
            tags = _clean_digest_text(str(details.get("Tags", "")))
            reason = _clean_digest_text(str(details.get("Reason", "")))
            summary = _clean_digest_text(str(details.get("Summary", "")))
            extras = [_clean_digest_text(str(extra)) for extra in item.get("extras", []) if _clean_digest_text(str(extra))]
            display_title = str(item.get("title") or "未命名条目")
            display_url = _escape(str(item.get("url") or "#"))
            parts.extend([
                '<div class="item-card">',
                f'<h3 class="item-title">{section_index}.{item_index} <a href="{display_url}">{_escape(display_title)}</a></h3>',
            ])
            if chips:
                parts.append('<div class="chips">' + ''.join(f'<span class="chip">{chip}</span>' for chip in chips) + '</div>')
            if tags:
                parts.append('<div class="label">\u6807\u7b7e</div>')
                parts.append(f'<p class="text">{_escape(tags)}</p>')
            if reason:
                parts.append('<div class="label">\u547d\u4e2d\u539f\u56e0</div>')
                parts.append(f'<p class="text">{_escape(reason)}</p>')
            if summary:
                parts.append('<div class="label">\u6458\u8981</div>')
                parts.append(f'<p class="text">{_escape(summary)}</p>')
            for extra in extras:
                parts.append('<div class="label">\u8865\u5145\u8bf4\u660e</div>')
                parts.append(f'<p class="text">{_escape(extra)}</p>')
            if item.get("url"):
                parts.append(f'<a class="button" href="{_escape(str(item.get("url")))}">\u67e5\u770b\u539f\u6587</a>')
            parts.append('</div>')

    if not sections:
        parts.extend([
            '<div class="panel">',
            '<h2>\u539f\u59cb\u5185\u5bb9</h2>',
            f'<pre style="white-space:pre-wrap;font-size:13px;line-height:1.7;color:#334155;">{_escape(raw_markdown or str(digest))}</pre>',
            '</div>',
        ])

    parts.extend([
        '<div class="footer">',
        f'{_escape(settings.app_name)} \u81ea\u52a8\u53d1\u9001 | \u4e3a\u4fdd\u8bc1\u517c\u5bb9\u6027\uff0c\u90ae\u4ef6\u540c\u65f6\u9644\u5e26\u7eaf\u6587\u672c\u7248\u672c\u3002',
        '</div>',
        '</div>',
        '</body>',
        '</html>',
    ])
    return "".join(parts)


def build_email_html(file_path: Path, subject: str) -> str:
    markdown = build_email_notice(file_path)
    digest = _parse_digest_markdown(markdown)
    digest["_raw_markdown"] = markdown
    return _render_email_html(digest, subject)


def send_digest_via_email(file_path: Path, subject: str) -> str:
    if not email_delivery_configured():
        return "email delivery skipped"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.app_name, settings.smtp_from))
    message["To"] = settings.smtp_to
    message.set_content(build_email_notice(file_path), charset="utf-8")
    message.add_alternative(build_email_html(file_path, subject), subtype="html", charset="utf-8")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    return f"email sent to {settings.smtp_to}"


def _escape(value: str) -> str:
    return html.escape((value or "").strip(), quote=True)


def _looks_english(text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", text or "")
    return len(letters) >= 12


def _translate_to_chinese(text: str, cache: dict[str, str]) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    if normalized in cache:
        return cache[normalized]
    if not _looks_english(normalized):
        cache[normalized] = normalized
        return normalized
    try:
        translated = GoogleTranslator(source="auto", target="zh-CN").translate(normalized)
        cache[normalized] = translated or normalized
        return translated or normalized
    except Exception:
        cache[normalized] = normalized
        return normalized


def _policy_item(item: Item) -> bool:
    return (
        item.category.startswith("Policy-")
        or item.source_id in {"gov-policy", "gov-news", "miit", "miit-policy", "ndrc-notice", "cac-gov"}
        or item.source_id.startswith("wechat-policy-")
    )


def _ai_item(item: Item) -> bool:
    prefixes = (
        "openai-",
        "anthropic-",
        "deepmind-",
        "huggingface-",
        "paperswithcode-",
        "reddit-",
        "hackernews-",
        "arxiv-",
        "github-",
        "wechat-ai-",
    )
    return item.category.startswith("AI-") or "Official-AI" in (item.subcategory or "") or item.source_id.startswith(prefixes)


def _friendly_summary(summary: str, translate_cache: dict[str, str]) -> str:
    normalized = re.sub(r"<[^>]+>", " ", summary or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    translated = _translate_to_chinese(normalized[:260], translate_cache)
    return translated[:140]


def _friendly_source(source: Source | None, source_id: str) -> str:
    if source and source.name:
        return source.name
    return source_id


def _friendly_tags(item: Item) -> str:
    return (item.subcategory or "").replace(",", " / ").strip(" /")


def _sort_dt(item: Item) -> datetime:
    return item.published_at or item.fetched_at or datetime.min


def _normalized_title(value: str) -> str:
    text = re.sub(r"[\W_]+", " ", (value or "").lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _same_event_key(item: Item) -> str:
    norm = _normalized_title(item.title or "")
    if not norm:
        return f"item-{item.id}"
    tokens = norm.split()
    return " ".join(tokens[:10]) if tokens else norm[:80]


def _topic_for_item(item: Item, kind: str) -> str:
    try:
        return topic_name_for_item(item, kind)
    except Exception:
        return item.category or ("Policy" if kind == "policy" else "AI")


def _ai_label(item: Item) -> str:
    text = " ".join(filter(None, [item.title or "", item.summary or "", item.subcategory or "", item.source_id or ""])).lower()
    tags = (item.subcategory or "").lower()
    if item.source_id.startswith("arxiv-") or "research" in tags or "paper" in text:
        return "\u7814\u7a76\u7a81\u7834"
    if item.source_id.startswith("github-") or "opensource" in tags or "open source" in text or "github" in text:
        return "\u5f00\u6e90\u9879\u76ee"
    if "model" in tags or "model" in text or "gpt" in text or "claude" in text or "gemini" in text:
        return "\u6a21\u578b\u53d1\u5e03"
    if "funding" in text or "raised" in text or "partner" in text or "acquisition" in text:
        return "\u878d\u8d44/\u5408\u4f5c"
    if "reddit-" in item.source_id or "hackernews-" in item.source_id:
        return "\u793e\u533a\u4fe1\u53f7"
    return "\u4ea7\u54c1\u66f4\u65b0"


def _policy_label(item: Item) -> str:
    text = " ".join(filter(None, [item.title or "", item.summary or "", item.subcategory or ""])).lower()
    if item.status in {"superseded", "inactive"} or item.replaced_by:
        return "\u66ff\u4ee3/\u5931\u6548"
    if "\u5f81\u6c42\u610f\u89c1" in text:
        return "\u5f81\u6c42\u610f\u89c1"
    if item.source_id.startswith("wechat-policy-"):
        return "\u653f\u7b56\u89e3\u8bfb"
    if item.source_id in OFFICIAL_POLICY_SOURCE_IDS or item.source_id.endswith("-policy"):
        return "\u6b63\u5f0f\u653f\u7b56"
    if "subsidy" in text or "\u8865\u8d34" in text or "\u57fa\u91d1" in text or "\u652f\u6301" in text:
        return "\u4ea7\u4e1a\u652f\u6301"
    if item.source_id in BROAD_POLICY_SOURCE_IDS:
        return "\u653f\u7b56\u89e3\u8bfb"
    return "\u5730\u65b9\u653f\u7b56" if item.region == "cn" else "\u653f\u7b56\u89e3\u8bfb"


def _item_label(item: Item, kind: str) -> str:
    return _policy_label(item) if kind == "policy" else _ai_label(item)


def _signal_label(item: Item) -> str:
    score = float(item.score or 0.0)
    if score >= 18:
        return "\u9ad8\u4f18\u5148\u7ea7"
    if score >= 12:
        return "\u503c\u5f97\u5173\u6ce8"
    return "\u8865\u5145\u89c2\u5bdf"


def _importance_line(item: Item, kind: str, label: str) -> str:
    if kind == "policy":
        mapping = {
            "\u6b63\u5f0f\u653f\u7b56": "\u5c5e\u4e8e\u6b63\u5f0f\u6587\u4ef6\uff0c\u53ef\u80fd\u76f4\u63a5\u5f71\u54cd\u5408\u89c4\u3001\u6267\u884c\u6216\u4ea7\u4e1a\u65b9\u5411\u3002",
            "\u5f81\u6c42\u610f\u89c1": "\u5904\u4e8e\u5f81\u6c42\u610f\u89c1\u9636\u6bb5\uff0c\u9002\u5408\u63d0\u524d\u5224\u65ad\u76d1\u7ba1\u65b9\u5411\u3002",
            "\u653f\u7b56\u89e3\u8bfb": "\u6709\u52a9\u4e8e\u7406\u89e3\u653f\u7b56\u53e3\u5f84\u3001\u843d\u5730\u65b9\u5f0f\u6216\u6267\u884c\u91cd\u70b9\u3002",
            "\u5730\u65b9\u653f\u7b56": "\u53cd\u6620\u5730\u65b9\u63a8\u8fdb\u8282\u594f\uff0c\u9002\u5408\u89c2\u5bdf\u533a\u57df\u843d\u5730\u8d8b\u52bf\u3002",
            "\u4ea7\u4e1a\u652f\u6301": "\u6d89\u53ca\u8d44\u91d1\u3001\u9879\u76ee\u6216\u4ea7\u4e1a\u652f\u6301\uff0c\u53ef\u80fd\u5f71\u54cd\u8d44\u6e90\u914d\u7f6e\u3002",
            "\u66ff\u4ee3/\u5931\u6548": "\u6d89\u53ca\u653f\u7b56\u751f\u547d\u5468\u671f\u53d8\u5316\uff0c\u503c\u5f97\u5c3d\u5feb\u786e\u8ba4\u5f53\u524d\u6709\u6548\u53e3\u5f84\u3002",
        }
        return mapping.get(label, "\u4e0e\u653f\u7b56\u6267\u884c\u6216\u76d1\u7ba1\u8d8b\u52bf\u76f8\u5173\uff0c\u503c\u5f97\u8ddf\u8e2a\u3002")
    mapping = {
        "\u6a21\u578b\u53d1\u5e03": "\u76f4\u63a5\u5f71\u54cd\u6a21\u578b\u80fd\u529b\u3001\u4ea7\u54c1\u8def\u7ebf\u6216\u7ade\u4e89\u683c\u5c40\u3002",
        "\u4ea7\u54c1\u66f4\u65b0": "\u6d89\u53ca\u4ea7\u54c1\u6216 API \u80fd\u529b\u53d8\u5316\uff0c\u503c\u5f97\u786e\u8ba4\u5b9e\u9645\u5f71\u54cd\u3002",
        "\u7814\u7a76\u7a81\u7834": "\u53ef\u80fd\u5f71\u54cd\u540e\u7eed\u6a21\u578b\u80fd\u529b\u3001\u65b9\u6cd5\u65b9\u5411\u6216\u8bc4\u6d4b\u57fa\u7ebf\u3002",
        "\u5f00\u6e90\u9879\u76ee": "\u53ef\u80fd\u6210\u4e3a\u53ef\u7528\u66ff\u4ee3\u65b9\u6848\u6216\u73b0\u6709\u7cfb\u7edf\u8865\u5145\u7ec4\u4ef6\u3002",
        "\u878d\u8d44/\u5408\u4f5c": "\u53ef\u80fd\u5f71\u54cd\u516c\u53f8\u6218\u7565\u3001\u751f\u6001\u5408\u4f5c\u6216\u8d44\u6e90\u6295\u5165\u65b9\u5411\u3002",
        "\u793e\u533a\u4fe1\u53f7": "\u80fd\u53cd\u6620\u5e02\u573a\u5173\u6ce8\u70b9\uff0c\u4f46\u9700\u8981\u7ed3\u5408\u4e00\u624b\u6765\u6e90\u5224\u65ad\u3002",
    }
    return mapping.get(label, "\u4e0e AI \u4ea7\u4e1a\u6216\u6a21\u578b\u8fdb\u5c55\u76f8\u5173\uff0c\u503c\u5f97\u8ddf\u8e2a\u3002")


def _soft_limit() -> int:
    return max(1200, min(int(settings.telegram_message_soft_limit or 3600), 3900))


def _limit_for(kind: str, daily: bool) -> int:
    if kind == "ai":
        if daily:
            return max(1, int(settings.telegram_daily_ai_limit or settings.telegram_ai_limit or 5))
        return max(1, int(settings.telegram_weekly_ai_limit or max(settings.telegram_ai_limit, 8) or 10))
    if daily:
        return max(1, int(settings.telegram_daily_policy_limit or settings.telegram_policy_limit or 5))
    return max(1, int(settings.telegram_weekly_policy_limit or max(settings.telegram_policy_limit, 8) or 10))


def _overview_title(kind: str, daily: bool) -> str:
    if kind == "ai":
        return "AI\u60c5\u62a5\u65e5\u62a5" if daily else "AI\u60c5\u62a5\u5468\u62a5"
    return "\u653f\u7b56\u60c5\u62a5\u65e5\u62a5" if daily else "\u653f\u7b56\u60c5\u62a5\u5468\u62a5"


def _overview_window(daily: bool) -> str:
    return "\u8fc7\u53bb 24 \u5c0f\u65f6" if daily else "\u8fc7\u53bb 7 \u5929"


def _published_label(item: Item) -> str:
    point = item.published_at or item.fetched_at
    return point.strftime("%Y-%m-%d %H:%M") if point else "\u672a\u77e5"


def prepare_delivery_items(items: list[Item]) -> list[Item]:
    """Keep one representative per news event before any delivery channel renders it."""
    selected: list[Item] = []
    ordered = sorted(items, key=lambda row: (row.score or 0.0, _sort_dt(row)), reverse=True)
    for item in ordered:
        if not (_ai_item(item) or _policy_item(item)):
            continue
        if any(
            items_look_duplicate(
                item.title,
                item.published_at or item.fetched_at,
                existing.title,
                existing.published_at or existing.fetched_at,
                item.url,
                existing.url,
            )
            for existing in selected
        ):
            continue
        selected.append(item)
    return selected


def _group_items(items: list[Item], kind: str, source_map: dict[str, Source]) -> list[dict[str, object]]:
    groups: dict[str, list[Item]] = {}
    for item in items:
        key = _same_event_key(item) if settings.telegram_enable_event_grouping else f"item-{item.id}"
        groups.setdefault(key, []).append(item)

    entries: list[dict[str, object]] = []
    for grouped_items in groups.values():
        ordered = sorted(grouped_items, key=lambda row: (row.score or 0.0, _sort_dt(row)), reverse=True)
        primary = ordered[0]
        source_names: list[str] = []
        for row in ordered:
            name = _friendly_source(source_map.get(row.source_id), row.source_id)
            if name not in source_names:
                source_names.append(name)
        label = _item_label(primary, kind)
        entries.append(
            {
                "item": primary,
                "kind": kind,
                "topic": _topic_for_item(primary, kind),
                "label": label,
                "signal": _signal_label(primary),
                "importance": _importance_line(primary, kind, label),
                "related_count": max(len(ordered) - 1, 0),
                "source_count": len(source_names),
                "source_names": source_names[:4],
            }
        )
    return sorted(entries, key=lambda row: ((row["item"].score or 0.0), _sort_dt(row["item"]), row["source_count"]), reverse=True)


def _select_entries(items: list[Item], kind: str, source_map: dict[str, Source], daily: bool) -> list[dict[str, object]]:
    entries = _group_items(items, kind, source_map)
    selected: list[dict[str, object]] = []
    topic_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    topic_cap = max(1, int(settings.telegram_max_items_per_topic or 3))
    source_cap = max(1, int(settings.telegram_max_items_per_source or 2))
    limit = _limit_for(kind, daily)

    for entry in entries:
        item = entry["item"]
        topic = str(entry["topic"])
        source_id = item.source_id
        if topic_counter[topic] >= topic_cap:
            continue
        if source_counter[source_id] >= source_cap:
            continue
        selected.append(entry)
        topic_counter[topic] += 1
        source_counter[source_id] += 1
        if len(selected) >= limit:
            break
    return selected


def _overview_lines(title: str, entries: list[dict[str, object]], daily: bool) -> list[str]:
    signal_counts = Counter(str(entry["signal"]) for entry in entries)
    topic_counts = Counter(str(entry["topic"]) for entry in entries if entry["topic"])
    source_count = len({entry["item"].source_id for entry in entries})
    high = "\u9ad8\u4f18\u5148\u7ea7"
    medium = "\u503c\u5f97\u5173\u6ce8"
    low = "\u8865\u5145\u89c2\u5bdf"
    conclusion = "\u4eca\u65e5\u7ed3\u8bba" if daily else "\u672c\u671f\u7ed3\u8bba"
    lines = [
        f"<b>{_escape(title)}</b>",
        f"{datetime.utcnow().strftime('%Y-%m-%d')} \u00b7 \u672c\u6b21\u65b0\u589e {len(entries)} \u6761 \u00b7 {source_count} \u4e2a\u6765\u6e90",
        f"<b>{conclusion}</b>",
        f"{high} {signal_counts.get(high, 0)} \u00b7 {medium} {signal_counts.get(medium, 0)} \u00b7 {low} {signal_counts.get(low, 0)}",
        "",
        "<b>\u5173\u6ce8\u4e3b\u9898</b>",
    ]
    for topic, count in topic_counts.most_common(3):
        lines.append(f"\u2022 {_escape(topic)}\uff08{count}\uff09")
    if not topic_counts:
        lines.append("\u2022 \u6682\u65e0\u660e\u663e\u4e3b\u9898\u96c6\u4e2d")
    lines.extend(["", "<i>\u4e0b\u65b9\u4e3a\u53bb\u91cd\u540e\u7684\u8be6\u60c5\uff0c\u6bcf\u4e2a\u4e8b\u4ef6\u4ec5\u5c55\u793a\u4e00\u6b21\u3002</i>"])
    return lines


def _entry_block(index: int, entry: dict[str, object], source_map: dict[str, Source], translate_cache: dict[str, str]) -> str:
    item: Item = entry["item"]
    translated_title = _translate_to_chinese(item.title, translate_cache)
    display_title = translated_title or item.title
    source_name = _friendly_source(source_map.get(item.source_id), item.source_id)
    meta = [f"\u7c7b\u578b: {entry['label']}", source_name, _published_label(item)]
    tags = _friendly_tags(item)
    if tags:
        meta.append(tags)
    if _policy_item(item) and item.status:
        meta.append(f"\u72b6\u6001\uff1a{item.status}")
    separator = " \u00b7 "
    lines = [
        f"<b>{index}. [{_escape(str(entry['signal']))}] {_escape(display_title)}</b>",
        f"<i>{_escape(separator.join(meta))}</i>",
    ]
    if translated_title and translated_title != item.title:
        lines.append(f"\u539f\u6587\uff1a{_escape(item.title)}")
    summary = _friendly_summary(item.summary or item.raw_content, translate_cache)
    if summary:
        lines.append(f"<blockquote>{_escape(summary)}</blockquote>")
    if entry["related_count"]:
        lines.append(f"\u540c\u4e8b\u4ef6\u6765\u6e90: {entry['source_count']} \u4e2a")
    lines.append('<a href="' + _escape(item.url) + '">\u9605\u8bfb\u539f\u6587</a>')
    return "\n".join(lines)


def _split_detail_messages(title: str, blocks: list[str]) -> list[str]:
    if not blocks:
        return []
    soft_limit = _soft_limit()
    chunks: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for block in blocks:
        block_length = len(block) + 2
        if current and current_length + block_length > soft_limit:
            chunks.append(current)
            current = [block]
            current_length = len(block)
        else:
            current.append(block)
            current_length += block_length
    if current:
        chunks.append(current)

    messages: list[str] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        part_title = f"{title}\uff08\u8be6\u60c5 {index}/{total}\uff09" if total > 1 else f"{title}\uff08\u8be6\u60c5\uff09"
        body = [f"<b>{_escape(part_title)}</b>", ""]
        for block in chunk:
            body.append(block)
            body.append("")
        messages.append("\n".join(body).strip())
    return messages


def _format_message(title: str, items: list[Item], source_map: dict[str, Source], translate_cache: dict[str, str], kind: str = "ai", daily: bool = True) -> str:
    entries = _select_entries(items, kind, source_map, daily)
    blocks = [_entry_block(index, entry, source_map, translate_cache) for index, entry in enumerate(entries, start=1)]
    detail_messages = _split_detail_messages(title, blocks)
    return detail_messages[0] if detail_messages else f"<b>{_escape(title)}\uff08\u8be6\u60c5\uff09</b>"


def _build_digest_messages(title: str, items: list[Item], source_map: dict[str, Source], translate_cache: dict[str, str], kind: str, daily: bool) -> list[str]:
    entries = _select_entries(items, kind, source_map, daily)
    if not entries:
        return []
    overview = "\n".join(_overview_lines(title, entries, daily)).strip()
    blocks = [_entry_block(index, entry, source_map, translate_cache) for index, entry in enumerate(entries, start=1)]
    detail_messages = _split_detail_messages(title, blocks)
    if settings.telegram_send_overview_first:
        return [overview, *detail_messages]
    return [*detail_messages, overview]


def _resolve_chat_id(kind: str) -> str:
    if kind == "ai" and settings.telegram_ai_chat_id:
        return settings.telegram_ai_chat_id
    if kind == "policy" and settings.telegram_policy_chat_id:
        return settings.telegram_policy_chat_id
    return settings.telegram_chat_id


def _resolve_ops_chat_id() -> str:
    return settings.telegram_ops_chat_id or settings.telegram_chat_id


def _send_telegram_message(chat_id: str, text: str) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML",
    }
    response = httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json=payload,
        timeout=40.0,
    )
    response.raise_for_status()


def send_split_telegram_digests(session: Session, daily: bool = True, items: list[Item] | None = None) -> list[str]:
    if not telegram_delivery_configured():
        return ["telegram delivery skipped"]

    rows = items if items is not None else session.execute(select(Item).order_by(Item.score.desc(), Item.fetched_at.desc()).limit(180)).scalars().all()
    rows = prepare_delivery_items(rows)[:180]
    if not rows:
        return ["telegram delivery skipped: no new items"]
    source_rows = session.execute(select(Source)).scalars().all()
    source_map = {row.id: row for row in source_rows}

    policy_items = [row for row in rows if _policy_item(row)]
    ai_items = [row for row in rows if _ai_item(row) and not _policy_item(row)]
    translate_cache: dict[str, str] = {}
    results: list[str] = []

    batches: list[tuple[str, list[Item], str]] = []
    if ai_items:
        batches.append((_overview_title("ai", daily), ai_items, "ai"))
    if policy_items:
        batches.append((_overview_title("policy", daily), policy_items, "policy"))

    for title, batch_items, kind in batches:
        chat_id = _resolve_chat_id(kind)
        if not chat_id:
            results.append(f"{kind} telegram skipped: missing chat id")
            continue
        messages = _build_digest_messages(title, batch_items, source_map, translate_cache, kind=kind, daily=daily)
        if not messages:
            results.append(f"{title} skipped: no ranked items")
            continue
        for message in messages:
            _send_telegram_message(chat_id, message)
        results.append(f"{title} sent to {chat_id} ({len(messages)} messages)")

    if not results:
        return ["telegram delivery skipped: no items available"]
    return results


def send_ops_alert(summary_lines: list[str]) -> str:
    if not ops_telegram_configured() or not summary_lines:
        return "ops alert skipped"
    chat_id = _resolve_ops_chat_id()
    body = ["<b>AI Policy Intel \u8fd0\u7ef4\u544a\u8b66</b>", *summary_lines]
    try:
        _send_telegram_message(chat_id, "\n".join(body)[:3900])
        return f"ops alert sent to {chat_id}"
    except Exception as exc:
        return f"ops alert failed: {exc}"
