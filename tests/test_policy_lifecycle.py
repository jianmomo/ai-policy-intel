from datetime import datetime

from app.db.models import Item
from app.policy_lifecycle import apply_policy_lifecycle, audit_policy_lifecycle, detect_policy_lifecycle



def build_item(**overrides):
    base = {
        'source_id': 'gov-policy',
        'title': '\u5173\u4e8e\u4fc3\u8fdb\u667a\u80fd\u4f53\u53d1\u5c55\u7684\u901a\u77e5',
        'url': 'https://example.com/policy',
        'normalized_url': 'https://example.com/policy',
        'published_at': datetime(2026, 6, 1),
        'fetched_at': datetime(2026, 6, 2),
        'category': 'Policy-CN',
        'subcategory': '',
        'region': 'cn',
        'summary': '',
        'reason': '',
        'score': 10.0,
        'raw_content': '',
        'hash': 'hash-value',
    }
    base.update(overrides)
    return Item(**base)



def test_detect_policy_lifecycle_active_with_fallback_effective_date() -> None:
    item = build_item()
    result = detect_policy_lifecycle(item)
    assert result['status'] == 'active'
    assert result['effective_at'] == datetime(2026, 6, 1)



def test_detect_policy_lifecycle_draft() -> None:
    item = build_item(title='\u5173\u4e8e\u67d0\u4e8b\u9879\u516c\u5f00\u5f81\u6c42\u610f\u89c1\u7684\u516c\u544a')
    result = detect_policy_lifecycle(item)
    assert result['status'] == 'draft'



def test_detect_policy_lifecycle_inactive_by_expiry() -> None:
    item = build_item(raw_content='\u672c\u901a\u77e5\u6709\u6548\u671f\u81f32024\u5e741\u67081\u65e5')
    result = detect_policy_lifecycle(item)
    assert result['status'] == 'inactive'
    assert result['expires_at'] == datetime(2024, 1, 1)



def test_apply_policy_lifecycle_preserves_replaced_by_link() -> None:
    item = build_item(replaced_by='\u65b0\u89c4\u5b9a')
    apply_policy_lifecycle(item)
    assert item.status == 'superseded'
    assert item.replaced_by == '\u65b0\u89c4\u5b9a'
    assert item.last_checked_at is not None



def test_apply_policy_lifecycle_respects_manual_override() -> None:
    item = build_item(
        override_enabled=True,
        override_status='inactive',
        override_reason='manual-check',
        override_replaced_by='\u4eba\u5de5\u8986\u76d6',
    )
    apply_policy_lifecycle(item)
    assert item.status == 'inactive'
    assert item.replaced_by == '\u4eba\u5de5\u8986\u76d6'
    assert item.status_reason == 'manual_override:manual-check'



def test_audit_policy_lifecycle_flags_unknown_and_stale() -> None:
    item = build_item(status='unknown', last_checked_at=datetime(2026, 5, 1))
    result = audit_policy_lifecycle(item, now=datetime(2026, 6, 29))
    assert 'unknown_status' in result['codes']
    assert 'stale_check' in result['codes']


def test_audit_policy_lifecycle_flags_missing_successor() -> None:
    item = build_item(status='superseded', replaced_by='A newer policy')
    result = audit_policy_lifecycle(item, successor_exists=False, now=datetime(2026, 6, 29))
    assert 'missing_successor' in result['codes']
