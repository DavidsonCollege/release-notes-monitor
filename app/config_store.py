"""Azure Table Storage wrapper for release-notes-monitor teams config.

Single entity (PartitionKey="config", RowKey="current"), JSON in "data" field.
ETag-based optimistic concurrency for admin edits.
"""
from __future__ import annotations
import json, os
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import DefaultAzureCredential

TABLE_NAME    = "releaseNotesConfig"
PARTITION_KEY = "config"
ROW_KEY       = "current"


def _table_client() -> TableClient:
    account = os.environ["STORAGE_ACCOUNT_NAME"]
    return TableClient(
        endpoint=f"https://{account}.table.core.windows.net",
        table_name=TABLE_NAME,
        credential=DefaultAzureCredential(),
    )


class ConfigStore:
    def __init__(self) -> None:
        self._client = _table_client()

    def read(self) -> dict:
        try:
            entity = self._client.get_entity(PARTITION_KEY, ROW_KEY)
        except ResourceNotFoundError:
            return {"teams": []}
        return json.loads(entity.get("data") or '{"teams": []}')

    def read_with_etag(self) -> dict:
        try:
            entity = self._client.get_entity(PARTITION_KEY, ROW_KEY)
        except ResourceNotFoundError:
            return {"config": {"teams": []}, "etag": None}
        return {
            "config": json.loads(entity.get("data") or '{"teams": []}'),
            "etag": entity.metadata.get("etag"),
        }

    def write(self, body: dict, etag: str | None = None) -> dict:
        entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": ROW_KEY,
            "data": json.dumps(body, ensure_ascii=False),
        }
        if etag:
            self._client.update_entity(entity, mode=UpdateMode.REPLACE,
                                       etag=etag, match_condition=MatchConditions.IfNotModified)
        else:
            self._client.upsert_entity(entity, mode=UpdateMode.REPLACE)
        return {"ok": True}
