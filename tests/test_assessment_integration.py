from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from tests.test_end_to_end import client, create_order_context, unwrap


def start(client, order=None):
    if order is None:
        _, _, order = create_order_context(client)
    template = unwrap(client.post('/api/damage-sop/templates', json={
        'brand': '通用', 'product_category': '数码', 'model_pattern': '*',
        'title': '检测流程', 'version': str(uuid4()), 'status': 'published',
        'steps': [{'step_code': 'PWR-040', 'title': '输入电压', 'instruction': 'INTERNAL_INSTRUCTION',
                   'fail_conclusion': 'INTERNAL_FAILURE', 'required': True}],
    }))
    data = unwrap(client.post('/api/damage-assessments', json={
        'repair_order_id': order['id'], 'template_id': template['template']['id'],
    }))
    return order, data


def complete(client, data, conclusion='电压偏低'):
    aid, rid = data['assessment']['id'], data['results'][0]['id']
    unwrap(client.patch(f'/api/damage-assessments/{aid}/results/{rid}', json={
        'result': 'fail', 'measured_value': '10.8', 'unit': 'V', 'notes': '复测一致',
    }))
    return unwrap(client.post(f'/api/damage-assessments/{aid}/complete', json={
        'conclusion': conclusion, 'responsibility': '待确认',
        'repair_recommendation': '检查连接器', 'estimated_cost': '280.00',
    }))


def test_completion_quote_snapshot_and_soft_delete(client):
    order, data = start(client)
    aid = data['assessment']['id']
    done = complete(client, data)
    assert done['order']['status'] == 'pending_quote'
    assert unwrap(client.get(f"/api/quotes?repair_order_id={order['id']}")) == []
    from app.core.database import SessionLocal
    from app.models.entities import ServiceTicketTimeline, RepairOrderStatusHistory
    from sqlalchemy import select
    with SessionLocal() as db:
        event = db.scalar(select(ServiceTicketTimeline).where(ServiceTicketTimeline.event_type == 'assessment_completed'))
        assert event.details_json['damage_assessment_id'] == aid
        assert event.actor_id is not None
        history = list(db.scalars(select(RepairOrderStatusHistory).where(RepairOrderStatusHistory.to_status == 'pending_quote')))
        assert len(history) == 1
    assert client.post(f'/api/damage-assessments/{aid}/complete', json={'conclusion': '重复'}).status_code == 409
    quote = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id']}))
    assert quote['damage_assessment_id'] == aid
    assert quote['assessment_result'] == '电压偏低'
    assert quote['assessment_responsibility'] == '待确认'
    assert quote['repair_recommendation'] == '检查连接器'
    assert Decimal(quote['total_amount']) == Decimal('280')
    assert len(quote['items']) == 1
    quote2 = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id'], 'assessment_result': None}))
    assert quote2['assessment_result'] is None
    assert quote2['items'] == []
    _, next_data = start(client, order)
    complete(client, next_data, '新结论')
    frozen = unwrap(client.get(f"/api/quotes/{quote['id']}"))
    assert frozen['assessment_result'] == '电压偏低'
    for ident in (aid, next_data['assessment']['id']):
        unwrap(client.delete(f'/api/damage-assessments/{ident}'))
    quote3 = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id']}))
    assert quote3['assessment_result'] is None
    assert quote3['damage_assessment_id'] is None
    assert quote3['items'] == []


@pytest.mark.parametrize('items', [[], [{'item_name': '已核价维修', 'unit_price': '120'}]])
def test_reviewed_items_do_not_receive_duplicate_estimate(client, items):
    order, data = start(client)
    complete(client, data)
    result = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id'], 'items': items}))
    assert len(result['items']) == len(items)
    assert Decimal(result['total_amount']) == (Decimal('120') if items else Decimal('0'))


def test_late_assessment_does_not_regress_order(client):
    order, data = start(client)
    unwrap(client.post(f"/api/orders/{order['id']}/status", json={'status': 'repairing'}))
    assert complete(client, data)['order']['status'] == 'repairing'


def test_quote_source_stays_with_reviewed_assessment(client):
    order, first = start(client)
    complete(client, first, '首次检查结论')
    preview = unwrap(client.get(f"/api/orders/{order['id']}/assessment-summary"))
    source_id = preview['assessment']['id']
    original = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id']}))
    _, second = start(client, order)
    complete(client, second, '第二次检查结论')
    # Saving an earlier preview must not silently relabel it with the latest ID.
    reviewed = unwrap(client.post('/api/quotes', json={
        'repair_order_id': order['id'], 'damage_assessment_id': source_id,
        'assessment_result': '人工核对后的首次结论', 'items': [],
    }))
    assert reviewed['damage_assessment_id'] == source_id
    assert reviewed['assessment_result'] == '人工核对后的首次结论'
    # The same rule applies when copying an old quote into a new version.
    copied = unwrap(client.post('/api/quotes', json={
        'repair_order_id': order['id'], 'damage_assessment_id': original['damage_assessment_id'],
        **{key: original[key] for key in ('assessment_result', 'assessment_responsibility', 'repair_recommendation')},
        'items': [],
    }))
    assert copied['damage_assessment_id'] == source_id
    assert copied['assessment_result'] == original['assessment_result']
    legacy = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id'], 'items': []}))
    assert legacy['damage_assessment_id'] == second['assessment']['id']
    assert legacy['assessment_result'] == '第二次检查结论'


def test_quote_explicit_no_source_does_not_claim_later_assessment(client):
    order, data = start(client)
    complete(client, data)
    quote = unwrap(client.post('/api/quotes', json={
        'repair_order_id': order['id'], 'damage_assessment_id': None,
        'assessment_result': '手工录入',
    }))
    assert quote['damage_assessment_id'] is None
    assert quote['assessment_result'] == '手工录入'
    assert quote['assessment_responsibility'] is None
    assert quote['items'] == []


@pytest.mark.parametrize('invalid_source', ['missing', 'unfinished', 'deleted', 'other_order'])
def test_invalid_quote_source_has_no_quote_side_effects(client, invalid_source):
    order, data = start(client)
    complete(client, data)
    draft = unwrap(client.post('/api/quotes', json={'repair_order_id': order['id']}))
    source_id = data['assessment']['id']
    if invalid_source == 'missing':
        source_id = 999999
    elif invalid_source == 'unfinished':
        _, pending = start(client, order)
        source_id = pending['assessment']['id']
    elif invalid_source == 'deleted':
        unwrap(client.delete(f'/api/damage-assessments/{source_id}'))
    else:
        customer, device = unwrap(client.get(f"/api/orders/{order['id']}"))['customer'], data['device']
        other = unwrap(client.post('/api/orders', json={
            'customer_id': customer['id'], 'device_id': device['id'], 'fault_description': '同设备另一次维修',
        }))
        _, other_data = start(client, other)
        complete(client, other_data)
        source_id = other_data['assessment']['id']
    response = client.post('/api/quotes', json={'repair_order_id': order['id'], 'damage_assessment_id': source_id})
    assert response.status_code == 409
    quotes = unwrap(client.get(f"/api/quotes?repair_order_id={order['id']}"))
    assert len(quotes) == 1
    assert quotes[0]['id'] == draft['id'] and quotes[0]['status'] == 'draft'


def test_suggestions_are_explicit_scoped_and_do_not_overwrite(client):
    order, data = start(client)
    aid, rid = data['assessment']['id'], data['results'][0]['id']
    diagnosis = unwrap(client.post('/api/diagnoses', json={
        'repair_order_id': order['id'], 'diagnosis_type': 'battery', 'severity': 'high',
        'confidence': '0.8', 'title': '日志电压异常', 'description': '需人工复测',
    }))
    detail = unwrap(client.get(f'/api/damage-assessments/{aid}'))
    assert detail['results'][0]['result'] == 'pending'
    assert detail['suggestions'][0]['suggested_step_code'] == 'PWR-040'
    url = f"/api/damage-assessments/{aid}/results/{rid}/adopt/{diagnosis['id']}"
    adopted = unwrap(client.post(url, json={'result': 'fail', 'notes': '已复测'}))
    assert adopted['source'] == 'log_rule'
    assert adopted['source_ref_json'] == {'diagnosis_id': diagnosis['id']}
    assert client.post(url, json={'result': 'pass'}).status_code == 409
    confirmed = unwrap(client.get(f"/api/diagnoses?repair_order_id={order['id']}"))[0]
    assert confirmed['confirmed_result'] == 'fail'
    assert confirmed['damage_assessment_id'] == aid
    assert confirmed['sop_step_code'] == 'PWR-040'
    manual = unwrap(client.patch(f'/api/damage-assessments/{aid}/results/{rid}', json={'result': 'pass'}))
    assert manual['source'] == 'manual' and manual['source_ref_json'] is None
    customer = unwrap(client.post('/api/customers', json={'name': '另一客户', 'phone': '13900000002'}))
    device = unwrap(client.post('/api/devices', json={'customer_id': customer['id'], 'brand': 'DJI', 'model': 'Air 3S', 'serial_number': 'OTHER'}))
    other = unwrap(client.post('/api/orders', json={'customer_id': customer['id'], 'device_id': device['id'], 'fault_description': '其他'}))
    _, second = start(client, other)
    wrong = f"/api/damage-assessments/{second['assessment']['id']}/results/{second['results'][0]['id']}/adopt/{diagnosis['id']}"
    assert client.post(wrong, json={'result': 'fail'}).status_code == 409


def test_pdf_uses_completed_assessment_and_excludes_internal_fields(client, monkeypatch):
    from app.reports.pdf import PdfReportService
    from tests.test_pdf_reports import _story_text
    from reportlab.platypus import Image as ReportImage
    order, data = start(client)
    aid, rid = data['assessment']['id'], data['results'][0]['id']
    unwrap(client.patch(f"/api/orders/{order['id']}/inspection", json={'internal_notes': 'INTERNAL_NOTES'}))
    buffer = BytesIO()
    Image.new('RGB', (80, 60), '#ff0000').save(buffer, format='PNG')
    attachment = unwrap(client.post(f"/api/attachments?repair_order_id={order['id']}", files={'file': ('evidence.png', buffer.getvalue(), 'image/png')}))
    unwrap(client.patch(f'/api/damage-assessments/{aid}/results/{rid}', json={
        'result': 'fail', 'measured_value': '10.8', 'unit': 'V', 'notes': '客户可见复测', 'evidence_attachment_id': attachment['id'],
    }))
    unwrap(client.post(f'/api/damage-assessments/{aid}/complete', json={'conclusion': '电压偏低'}))
    original = PdfReportService._atomic_build
    captured = {}
    def capture(self, path, story, *args, **kwargs):
        captured['text'] = _story_text(story)
        def image_count(items):
            return sum(1 if isinstance(x, ReportImage) else image_count(getattr(x, '_content', [])) for x in items)
        captured['images'] = image_count(story)
        return original(self, path, story, *args, **kwargs)
    monkeypatch.setattr(PdfReportService, '_atomic_build', capture)
    report = unwrap(client.post(f"/api/orders/{order['id']}/reports/inspection"))
    assert client.get(report['download_url']).content.startswith(b'%PDF')
    for expected in ('检测明细', '输入电压', '异常', '10.8 V', '客户可见复测', '电压偏低'):
        assert expected in captured['text']
    for forbidden in ('INTERNAL_NOTES', 'INTERNAL_INSTRUCTION', 'INTERNAL_FAILURE'):
        assert forbidden not in captured['text']
    assert captured['images'] == 1


def test_assessment_migration_round_trip_preserves_old_data(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'migration.db'
    env = dict(os.environ, DATABASE_URL=f'sqlite:///{database}')
    def migrate(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=root, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    migrate('upgrade', 'head')
    migrate('downgrade', '0030_forum_recommendation_signals')
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO customers (customer_no,name,phone,customer_type,created_at,updated_at) VALUES ('CU-MIGRATION','迁移前客户','13000000000','individual','2026-10-25','2026-10-25')")
        assert 'damage_assessment_id' not in [r[1] for r in db.execute('PRAGMA table_info(quotes)')]
    migrate('upgrade', 'head')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT name FROM customers').fetchone()[0] == '迁移前客户'
        assert 'damage_assessment_id' in [r[1] for r in db.execute('PRAGMA table_info(quotes)')]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    migrate('downgrade', '0030_forum_recommendation_signals')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT name FROM customers').fetchone()[0] == '迁移前客户'

@pytest.mark.parametrize('role', ['viewer', 'call_operator', 'finance', 'warehouse', 'engineer', 'technical_support', 'manager'])
def test_assessment_write_roles_preserve_read_access(client, role):
    order, data = start(client)
    aid, rid = data['assessment']['id'], data['results'][0]['id']
    user = unwrap(client.post('/api/users', json={
        'username': f'assessment-{role}', 'display_name': role,
        'role': role, 'password': 'RoleTest12345',
    }))
    # Give every role the same order scope, independently of write capability.
    from app.core.database import SessionLocal
    from app.models.entities import RepairOrder
    with SessionLocal() as db:
        db.get(RepairOrder, order['id']).engineer_id = user['id']
        db.commit()
    login = unwrap(client.post('/api/auth/login', json={
        'username': user['username'], 'password': user['generated_password'],
    }))
    client.headers.update({'X-CSRF-Token': login['csrf_token']})
    assert unwrap(client.get(f'/api/damage-assessments/{aid}'))['assessment']['id'] == aid
    assert any(x['id'] == aid for x in unwrap(client.get('/api/damage-assessments')))
    expected = 403 if role in {'viewer', 'call_operator', 'finance', 'warehouse'} else None
    created = client.post('/api/damage-assessments', json={
        'repair_order_id': order['id'], 'template_id': data['template']['id'],
    })
    assert created.status_code == (expected or 201), created.text
    updated = client.patch(f'/api/damage-assessments/{aid}/results/{rid}', json={'result': 'pass'})
    assert updated.status_code == (expected or 200), updated.text
    completed = client.post(f'/api/damage-assessments/{aid}/complete', json={'conclusion': '检测通过'})
    assert completed.status_code == (expected or 200), completed.text
    if expected:
        after = unwrap(client.get(f'/api/damage-assessments/{aid}'))
        assert after['assessment']['status'] == 'in_progress'
        assert after['results'][0]['result'] == 'pending'
        assert len(unwrap(client.get('/api/damage-assessments'))) == 1
