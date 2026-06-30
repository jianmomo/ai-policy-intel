from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.base import SessionLocal
from app.db.models import Item, Source
from app.main import app

client = TestClient(app)



def ensure_policy_source() -> None:
    with SessionLocal() as session:
        source = session.get(Source, 'gov-policy')
        if source is None:
            session.add(
                Source(
                    id='gov-policy',
                    name='China Government Policy',
                    category='policy',
                    region='cn',
                    type='rss',
                    url='https://example.com',
                    enabled=True,
                    priority=10,
                    tags='policy,official',
                )
            )
            session.commit()



def create_policy_item() -> int:
    ensure_policy_source()
    unique = uuid4().hex
    with SessionLocal() as session:
        item = Item(
            source_id='gov-policy',
            title=f'Policy lifecycle test {unique}',
            url=f'https://example.com/{unique}',
            normalized_url=f'https://example.com/{unique}',
            published_at=datetime(2026, 6, 1),
            fetched_at=datetime(2026, 6, 2),
            category='Policy-Central',
            subcategory='policy,test',
            region='cn',
            summary='override flow test',
            reason='test reason',
            score=12.0,
            raw_content='about policy',
            hash=f'hash-{unique}',
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item.id



def test_ui_dashboard_route() -> None:
    response = client.get('/ui')
    assert response.status_code == 200
    assert 'AI Policy Intel' in response.text
    assert '首页看板' in response.text
    assert '<built-in method items' not in response.text



def test_ui_dashboard_en_route() -> None:
    response = client.get('/ui?lang=en')
    assert response.status_code == 200
    assert 'Dashboard | AI Policy Intel' in response.text
    assert 'Language' in response.text
    assert 'Source Coverage' in response.text



def test_ui_policy_route() -> None:
    response = client.get('/ui/policies')
    assert response.status_code == 200
    assert '政策库' in response.text
    assert 'effective_at' in response.text



def test_ui_policy_en_route() -> None:
    response = client.get('/ui/policies?lang=en')
    assert response.status_code == 200
    assert 'Policy Library | AI Policy Intel' in response.text
    assert 'Lifecycle breakdown' in response.text



def test_ui_ai_route() -> None:
    response = client.get('/ui/ai')
    assert response.status_code == 200
    assert 'AI Policy Intel' in response.text
    assert '情报库' in response.text



def test_ui_ai_en_route() -> None:
    response = client.get('/ui/ai?lang=en')
    assert response.status_code == 200
    assert 'AI Intel Library | AI Policy Intel' in response.text
    assert 'Filter' in response.text



def test_ui_source_coverage_route() -> None:
    response = client.get('/ui/sources?lang=en&days=7')
    assert response.status_code == 200
    assert 'Source Coverage | AI Policy Intel' in response.text
    assert 'Coverage window' in response.text



def test_ui_ai_topics_route() -> None:
    response = client.get('/ui/ai/topics?lang=en&days=90&track=all&sort=coverage')
    assert response.status_code == 200
    assert 'AI Topics | AI Policy Intel' in response.text
    assert 'Topic Clusters' in response.text
    assert 'Sort by coverage' in response.text



def test_ui_policy_topics_route() -> None:
    response = client.get('/ui/policies/topics?lang=en&days=365')
    assert response.status_code == 200
    assert 'Policy Topics | AI Policy Intel' in response.text
    assert 'Back to library' in response.text



def test_ui_policy_detail_route_en() -> None:
    item_id = create_policy_item()
    response = client.get(f'/ui/items/{item_id}?lang=en')
    assert response.status_code == 200
    assert 'Policy timeline' in response.text
    assert 'Policy relations' in response.text
    assert 'Auto check' in response.text



def test_admin_policy_list_route_local_access() -> None:
    response = client.get('/ui/admin/policies?lang=en')
    assert response.status_code == 200
    assert 'Lifecycle Overrides' in response.text



def test_admin_policy_queue_route_local_access() -> None:
    response = client.get('/ui/admin/policy-queue?lang=en&window=14')
    assert response.status_code == 200
    assert 'Policy Queue | AI Policy Intel' in response.text
    assert 'Expiry window' in response.text
    assert 'Needs recheck' in response.text



def test_admin_policy_relations_route_local_access() -> None:
    response = client.get('/ui/admin/policy-relations?lang=en')
    assert response.status_code == 200
    assert 'Policy Relations | AI Policy Intel' in response.text
    assert 'Relation groups' in response.text



def test_admin_source_route_local_access() -> None:
    response = client.get('/ui/admin/sources?lang=en')
    assert response.status_code == 200
    assert 'Source Admin | AI Policy Intel' in response.text
    assert 'Enable collection' in response.text



def test_admin_runs_route_local_access() -> None:
    response = client.get('/ui/admin/runs?lang=en')
    assert response.status_code == 200
    assert 'Run Audit | AI Policy Intel' in response.text
    assert 'Backup Archives' in response.text



def test_admin_policy_override_save() -> None:
    item_id = create_policy_item()
    response = client.post(
        f'/ui/admin/policies/{item_id}',
        data={
            'lang': 'en',
            'override_enabled': 'on',
            'override_status': 'inactive',
            'override_effective_at': '2026-06-03',
            'override_expires_at': '2026-12-31',
            'override_replaced_by': 'Manual successor',
            'override_reason': 'operator review',
            'action': 'save',
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as session:
        item = session.get(Item, item_id)
        assert item is not None
        assert item.override_enabled is True
        assert item.status == 'inactive'
        assert item.replaced_by == 'Manual successor'
        assert item.override_reason == 'operator review'
        assert item.override_effective_at == datetime(2026, 6, 3)
        assert item.override_expires_at == datetime(2026, 12, 31)



def test_ui_ai_topics_history_route() -> None:
    response = client.get('/ui/ai/topics/history?lang=en&days=90&sort=rising&trend=all')
    assert response.status_code == 200
    assert 'AI Trend History | AI Policy Intel' in response.text
    assert 'Back to topics' in response.text
    assert 'Sort by rise' in response.text


def test_ui_policy_topics_history_route() -> None:
    response = client.get('/ui/policies/topics/history?lang=en&days=365')
    assert response.status_code == 200
    assert 'Policy Trend History | AI Policy Intel' in response.text
    assert 'Back to topics' in response.text
