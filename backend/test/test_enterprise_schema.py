import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.enterprise_schema import (
    ENTERPRISE_TABLES,
    ImageTaskBatchModel,
    ImageTaskItemModel,
    UpstreamAccountModel,
    UsageLedgerModel,
    create_enterprise_schema,
    schema_summary,
)


class EnterpriseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        create_enterprise_schema(engine=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_schema_creates_all_enterprise_tables(self) -> None:
        table_names = set(inspect(self.engine).get_table_names())
        self.assertEqual(table_names, set(ENTERPRISE_TABLES))
        self.assertEqual(
            table_names,
            {
                "image_assets",
                "image_task_batches",
                "image_task_events",
                "image_task_items",
                "reference_image_assets",
                "upstream_accounts",
                "usage_ledger",
            },
        )

    def test_batch_task_usage_and_account_rows_can_be_persisted(self) -> None:
        session = self.Session()
        try:
            session.add(
                ImageTaskBatchModel(
                    id="batch-1",
                    owner_id="owner-1",
                    created_by="user-1",
                    client_request_id="request-1",
                    requested_count=2,
                )
            )
            session.add(
                ImageTaskItemModel(
                    id="task-1",
                    batch_id="batch-1",
                    owner_id="owner-1",
                    position=0,
                    payload_json={"prompt": "test"},
                )
            )
            session.add(
                UsageLedgerModel(
                    id="usage-1",
                    owner_id="owner-1",
                    batch_id="batch-1",
                    task_id="task-1",
                    event_type="reserve",
                    units=-1,
                    idempotency_key="reserve-task-1",
                )
            )
            session.add(
                UpstreamAccountModel(
                    id="account-1",
                    provider="relay",
                    account_ref="relay-account-1",
                    credential_ref="secret-manager://relay/account-1",
                    max_concurrency=3,
                )
            )
            session.commit()

            self.assertEqual(session.query(ImageTaskBatchModel).count(), 1)
            self.assertEqual(session.query(ImageTaskItemModel).count(), 1)
            self.assertEqual(session.query(UsageLedgerModel).count(), 1)
            self.assertEqual(session.query(UpstreamAccountModel).count(), 1)
        finally:
            session.close()

    def test_batch_idempotency_constraint_rejects_duplicates(self) -> None:
        session = self.Session()
        try:
            session.add_all(
                [
                    ImageTaskBatchModel(
                        id="batch-1",
                        owner_id="owner-1",
                        created_by="user-1",
                        client_request_id="same-request",
                    ),
                    ImageTaskBatchModel(
                        id="batch-2",
                        owner_id="owner-1",
                        created_by="user-1",
                        client_request_id="same-request",
                    ),
                ]
            )
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_schema_summary_contains_indexes_and_columns(self) -> None:
        summary = {item["table"]: item for item in schema_summary()}
        self.assertIn("status", summary["image_task_items"]["columns"])
        self.assertIn("ix_image_task_status_scheduled", summary["image_task_items"]["indexes"])


if __name__ == "__main__":
    unittest.main()
