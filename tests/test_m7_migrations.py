import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_legacy_requisition_survives_draft_migration():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_target = [('requisitions', '0001_initial')]
    try:
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps
        model = old_apps.get_model('requisitions', 'Requisition')
        record = model.objects.create(requisition_number='REQ-000987', requester_name='Legacy',
            requester_phone='+233240001122', purpose='Existing request', description='Preserved',
            amount_requested='300.00', status='SUBMITTED')
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new_apps = executor.loader.project_state(latest).apps
        saved = new_apps.get_model('requisitions', 'Requisition').objects.get(pk=record.pk)
        assert saved.requisition_number == 'REQ-000987'
        assert saved.amount_requested == record.amount_requested or str(saved.amount_requested) == '300.00'
        assert saved.status == 'SUBMITTED'
        assert saved.access_version
        assert saved.creation_key is None
    finally:
        MigrationExecutor(connection).migrate(latest)
