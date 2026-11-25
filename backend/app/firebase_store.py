from __future__ import annotations

import ast
import base64
import binascii
from datetime import datetime
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote

import google.auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account
import requests
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


FIREBASE_DATABASE_SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]
REQUEST_TIMEOUT_SECONDS = 30
logger = logging.getLogger(__name__)


class FirebaseStoreError(RuntimeError):
    pass


class FirebaseRealtimeStore:
    def __init__(
        self,
        database_url: str,
        service_account_path: str = "",
        service_account_json_b64: str = "",
    ) -> None:
        if not database_url.strip():
            raise FirebaseStoreError("Set FIREBASE_DATABASE_URL to your Realtime Database URL.")
        self.database_url = database_url.strip().rstrip("/")

        if self.database_url.endswith(".json"):
            raise FirebaseStoreError(
                "Invalid FIREBASE_DATABASE_URL: remove the .json suffix. "
        "The Firebase REST API adds .json automatically."
            )
        self.service_account_path = service_account_path.strip()
        self.service_account_json_b64 = service_account_json_b64.strip()
        self._credentials = None
        self._credentials_lock = Lock()

    def _access_token(self) -> str:
        with self._credentials_lock:
            try:
                    if self._credentials is None:
                        if self.service_account_json_b64:
                            try:
                                decoded_json = base64.b64decode(
                                    self.service_account_json_b64,
                                    validate=True,
                                )
                                service_account_info = json.loads(decoded_json.decode("utf-8"))

                                if not isinstance(service_account_info, dict):
                                    raise ValueError("Service-account JSON must be an object.")

                                self._credentials = service_account.Credentials.from_service_account_info(
                                    service_account_info,
                                    scopes=FIREBASE_DATABASE_SCOPES,
                                )
                            except (
                                binascii.Error,
                                UnicodeDecodeError,
                                json.JSONDecodeError,
                                ValueError,
                                KeyError,
                                TypeError,
                            ) as exc:
                                raise FirebaseStoreError(
                                    "FIREBASE_SERVICE_ACCOUNT_JSON must contain valid Base64-encoded "
                                    "Firebase service-account JSON."
                            ) from exc

                        elif self.service_account_path:
                        self._credentials = service_account.Credentials.from_service_account_file(
                            str(Path(self.service_account_path).resolve()),
                            scopes=FIREBASE_DATABASE_SCOPES,
                        )
                        else:
                            self._credentials, _ = google.auth.default(
                                scopes=FIREBASE_DATABASE_SCOPES
                        )
                if not self._credentials.valid:
                    self._credentials.refresh(GoogleRequest())
            except (GoogleAuthError, OSError, ValueError, requests.RequestException) as exc:
                raise FirebaseStoreError(
                    "Firebase credentials are unavailable. Configure FIREBASE_SERVICE_ACCOUNT_PATH "
                    "or Google Application Default Credentials."
                ) from exc
            token = self._credentials.token
        if not token:
            raise FirebaseStoreError("Google credentials did not provide a Firebase access token.")
        return token

    def _url(self, path: str = "") -> str:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/") if part)
        if not encoded_path:
            return f"{self.database_url}/.json"
        return f"{self.database_url}/{encoded_path}.json"

    def _request(
        self,
        method: str,
        path: str = "",
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> requests.Response:

        query = params.copy() if params else {}
        request_headers = {"Authorization": f"Bearer {self._access_token()}"}
        if headers:
            request_headers.update(headers)

        try:
            response = requests.request(
                method,
                self._url(path),
                params=query,
                headers=request_headers,
                json=json_body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            response.raise_for_status()
            return response

        except requests.RequestException as exc:
            response = getattr(exc, "response", None)

            if response is not None:
                raise FirebaseStoreError(
                    f"Firebase request failed (HTTP {response.status_code})."
                ) from exc

            raise FirebaseStoreError(
                f"Firebase connection failed while sending a {method} request."
            ) from exc

    def read_root(self) -> dict[str, Any]:
        try:
            result = self._request("GET").json()
        except requests.JSONDecodeError as exc:
            raise FirebaseStoreError("Firebase returned an invalid database response.") from exc
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise FirebaseStoreError("The Firebase database root must be a JSON object.")
        return result

    def update_paths(self, updates: dict[str, Any]) -> None:
        if updates:
            self._request("PATCH", json_body=updates)

    def set_node(self, path: str, value: Any) -> None:
        self._request("PUT", path, json_body=value)

    def allocate_id(self, table_name: str, minimum: int) -> int:
        path = f"_meta/counters/{table_name}"
        for _ in range(10):
            response = self._request(
                "GET",
                path,
                headers={"X-Firebase-ETag": "true"},
            )
            try:
                current = response.json()
            except requests.JSONDecodeError as exc:
                raise FirebaseStoreError("Firebase returned an invalid ID counter.") from exc
            etag = response.headers.get("ETag")
            if not etag:
                raise FirebaseStoreError("Firebase did not return an ETag for its ID counter.")
            next_value = max(int(current or 0), minimum) + 1
            try:
                self._request(
                    "PUT",
                    path,
                    headers={"if-match": etag},
                    json_body=next_value,
                )
                return next_value
            except FirebaseStoreError as exc:
                if "HTTP 412" not in str(exc):
                    raise
        raise FirebaseStoreError(f"Could not allocate a unique ID for the {table_name} table.")


def _primary_key_value(table, row: dict[str, Any]) -> str:
    values = [str(row[column.name]) for column in table.primary_key.columns]
    if len(values) == 1 and table.primary_key.columns[0].name == "id":
        return values[0]
    encoded = json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _rtdb_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [row for row in value.values() if isinstance(row, dict)]
    return []


def _from_firebase(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, LargeBinary):
        if isinstance(value, str):
            if value.startswith(("b'", 'b"')) and value[-1:] == value[1:2]:
                try:
                    legacy_bytes = ast.literal_eval(value)
                except (SyntaxError, ValueError) as exc:
                    raise FirebaseStoreError(
                        f"Invalid binary data in {column.table.name}.{column.name}."
                    ) from exc
                if isinstance(legacy_bytes, bytes):
                    return legacy_bytes
            try:
                return base64.b64decode(value, validate=True)
            except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
                raise FirebaseStoreError(
                    f"Binary data in {column.table.name}.{column.name} must be base64 text."
                ) from exc
        raise FirebaseStoreError(f"Binary data in {column.table.name}.{column.name} must be base64 text.")
    if isinstance(column.type, DateTime):
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise FirebaseStoreError(
                f"Invalid timestamp in {column.table.name}.{column.name}."
            ) from exc
    if isinstance(column.type, Boolean):
        return bool(value)
    if isinstance(column.type, Integer) and value is not None:
        return int(value)
    return value


def _to_firebase(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, LargeBinary):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    raise FirebaseStoreError(
        f"Unsupported value in {column.table.name}.{column.name}: {type(value).__name__}."
    )


def create_firebase_session(base, store: FirebaseRealtimeStore) -> Session:
    snapshot = store.read_root()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    base.metadata.create_all(engine)
    model_by_table = {
        mapper.local_table.name: mapper.class_
        for mapper in base.registry.mappers
    }
    max_ids: dict[str, int] = {}
    with engine.begin() as connection:
        for table in base.metadata.sorted_tables:
            rows = _rtdb_rows(snapshot.get(table.name))
            if not rows:
                continue
            rows_by_primary_key: dict[tuple[str, ...], dict[str, Any]] = {}
            for row in rows:
                primary_key = tuple(
                    str(row[column.name])
                    for column in table.primary_key.columns
                    if column.name in row
                )
                if len(primary_key) != len(table.primary_key.columns):
                    raise FirebaseStoreError(
                        f"Firebase row in {table.name} is missing a primary key."
                    )
                if primary_key in rows_by_primary_key:
                    logger.warning(
                        "Found duplicate Firebase row in %s with primary key %s; "
                        "using the last row.",
                        table.name,
                        primary_key,
                    )
                rows_by_primary_key[primary_key] = row

            seen_job_keys: set[tuple[Any, Any]] = set()
            for row in rows_by_primary_key.values():
                values = {
                    column.name: _from_firebase(column, row[column.name])
                    for column in table.columns
                    if column.name in row
                }
                if values:
                    if len(table.primary_key.columns) == 1:
                        key_column = next(iter(table.primary_key.columns))
                        if key_column.name == "id" and values.get("id") is not None:
                            max_ids[table.name] = max(max_ids.get(table.name, 0), int(values["id"]))
                    orphaned_reference = next(
                        (
                            foreign_key
                            for foreign_key in table.foreign_keys
                            if values.get(foreign_key.parent.name) is not None
                            and connection.execute(
                                select(foreign_key.column).where(
                                    foreign_key.column == values[foreign_key.parent.name]
                                )
                            ).first()
                            is None
                        ),
                        None,
                    )
                    if orphaned_reference is not None:
                        logger.warning(
                            "Skipping orphaned Firebase row in %s with id=%s; "
                            "%s references missing %s.%s=%s.",
                            table.name,
                            values.get("id", tuple(values.get(column.name) for column in table.primary_key.columns)),
                            orphaned_reference.parent.name,
                            orphaned_reference.column.table.name,
                            orphaned_reference.column.name,
                            values[orphaned_reference.parent.name],
                        )
                        continue
                    if table.name == "jobs":
                        unique_key = (values.get("source_url"), values.get("title"))
                        if all(value is not None for value in unique_key):
                            if unique_key in seen_job_keys:
                                logger.warning(
                                    "Skipping duplicate Firebase job row id=%s; "
                                    "source_url and title must be unique.",
                                    values.get("id"),
                                )
                                continue
                            seen_job_keys.add(unique_key)
                    connection.execute(table.insert(), values)

    class FirebaseSession(Session):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._firebase_store = store
            self._firebase_tables = base.metadata.tables
            self._firebase_models = model_by_table
            self._firebase_max_ids = max_ids.copy()
            self._firebase_pending: dict[int, tuple[str, Any]] = {}

        def flush(self, objects=None) -> None:
            changed = list(self.new) + list(self.dirty) + list(self.deleted)
            for instance in changed:
                table = instance.__table__
                pk_columns = list(table.primary_key.columns)
                if (
                    instance in self.new
                    and len(pk_columns) == 1
                    and pk_columns[0].name == "id"
                    and getattr(instance, pk_columns[0].key) is None
                ):
                    next_id = self._firebase_store.allocate_id(
                        table.name,
                        self._firebase_max_ids.get(table.name, 0),
                    )
                    setattr(instance, pk_columns[0].key, next_id)
                    self._firebase_max_ids[table.name] = next_id
                action = "delete" if instance in self.deleted else "set"
                self._firebase_pending[id(instance)] = (action, instance)
            super().flush(objects)

        def commit(self) -> None:
            self.flush()
            updates: dict[str, Any] = {}
            for action, instance in self._firebase_pending.values():
                table = instance.__table__
                mapper = self._firebase_models[table.name].__mapper__
                row = {
                    prop.columns[0].name: _to_firebase(
                        prop.columns[0],
                        getattr(instance, prop.key),
                    )
                    for prop in mapper.column_attrs
                }
                key = _primary_key_value(table, row)
                updates[f"{table.name}/{key}"] = None if action == "delete" else row
            try:
                self._firebase_store.update_paths(updates)
            except FirebaseStoreError:
                super().rollback()
                self._firebase_pending.clear()
                raise
            try:
                super().commit()
            finally:
                self._firebase_pending.clear()

        def rollback(self) -> None:
            self._firebase_pending.clear()
            super().rollback()

        def close(self) -> None:
            try:
                super().close()
            finally:
                self.get_bind().dispose()

    return FirebaseSession(bind=engine, expire_on_commit=False)


def export_rows_to_firebase(
    store: FirebaseRealtimeStore,
    data: dict[str, Any],
    base,
    *,
    replace_tables: bool = False,
) -> None:
    updates: dict[str, Any] = {}
    for table_name, value in data.items():
        if table_name == "_meta":
            continue
        table = base.metadata.tables.get(table_name)
        if table is None:
            raise FirebaseStoreError(f"Unknown table in import: {table_name}.")
        rows = _rtdb_rows(value)
        table_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not row:
                continue
            if any(column.name not in row for column in table.primary_key.columns):
                raise FirebaseStoreError(
                    f"Cannot determine a Firebase key for a row in {table_name}."
                )
            row_values = {
                column.name: row[column.name]
                for column in table.columns
                if column.name in row
            }
            table_rows[_primary_key_value(table, row_values)] = row_values
        if replace_tables:
            store.set_node(table_name, table_rows or None)
        else:
            updates[table_name] = table_rows
    store.update_paths(updates)
