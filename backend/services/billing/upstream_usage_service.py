from __future__ import annotations

import hashlib
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Callable

from curl_cffi import requests
from sqlalchemy import and_, case, create_engine, func, inspect, or_, select, text
from sqlalchemy.orm import sessionmaker

from services.accounts.user_service import user_service
from services.billing.upstream_usage_models import BillingBase, UpstreamUsageRecord, UpstreamUsageSyncState
from services.platform.config import config
from services.platform.enterprise_schema import resolve_enterprise_database_url
from services.platform.proxy_service import proxy_settings
from utils.log import logger


PROVIDER = "lk888"
DEFAULT_USAGE_URL = "https://api.lk888.ai/v1/skills/usage"
MONEY_QUANTUM = Decimal("0.000000000001")
MODEL_TYPES = {"image", "video", "chat", "audio"}
SUCCESS_STATES = {"success", "succeeded", "completed", "complete"}
FAILED_STATES = {"failed", "error"}


class UpstreamUsageError(RuntimeError):
    pass


class UpstreamUsageNotConfigured(UpstreamUsageError):
    pass


class UpstreamUsageSyncInProgress(UpstreamUsageError):
    pass


@dataclass(frozen=True)
class UpstreamUsageSettings:
    enabled: bool
    usage_url: str
    scope: str
    interval_secs: int
    lookback_days: int
    full_lookback_days: int
    timeout_secs: int
    page_size: int
    lease_secs: int


def _clean(value: object, default: str = "", limit: int = 191) -> str:
    return (str(value if value is not None else "").strip() or default)[:limit]


def _model_version(value: object) -> str:
    normalized = _clean(value, limit=191).lower()
    for version in ("flare", "sunburst"):
        if normalized == version or normalized.endswith(f"-{version}"):
            return version
    return ""


def _usage_model_version(item: dict[str, Any]) -> str:
    version = _clean(item.get("model_version") or item.get("version"), limit=64).lower()
    if version in {"flare", "sunburst"}:
        return version
    params = item.get("params")
    if isinstance(params, dict):
        version = _clean(params.get("version"), limit=64).lower()
        if version in {"flare", "sunburst"}:
            return version
    return _model_version(item.get("model"))


def _display_model(
    upstream_model: object,
    requested_model: object = "",
    model_version: object = "",
    model_type: object = "",
) -> str:
    requested = _clean(requested_model, limit=191)
    if requested:
        value = requested
    else:
        value = _clean(upstream_model, "unknown", 191)
    version = _clean(model_version, limit=64).lower()
    if version in {"flare", "sunburst"} and value.lower() in {"tt-image-2.5", "gpt-image-2.5"}:
        return f"gpt-image-2.5-{version}"
    normalized = value.lower()
    if _clean(model_type, limit=32).lower() == "chat" and normalized.startswith("tt-"):
        return f"gpt-{value[3:]}"
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(maximum, value))


def upstream_usage_settings() -> UpstreamUsageSettings:
    usage_url = _clean(os.getenv("UPSTREAM_COST_SYNC_URL"), DEFAULT_USAGE_URL, 1000).rstrip("/")
    scope = _clean(os.getenv("UPSTREAM_COST_SYNC_SCOPE"), "user", 16).lower()
    if scope not in {"key", "user"}:
        scope = "user"
    return UpstreamUsageSettings(
        enabled=_bool_env("UPSTREAM_COST_SYNC_ENABLED", False),
        usage_url=usage_url,
        scope=scope,
        interval_secs=_int_env("UPSTREAM_COST_SYNC_INTERVAL_SECS", 300, 60, 86_400),
        lookback_days=_int_env("UPSTREAM_COST_SYNC_LOOKBACK_DAYS", 2, 1, 30),
        full_lookback_days=_int_env("UPSTREAM_COST_SYNC_FULL_LOOKBACK_DAYS", 30, 1, 30),
        timeout_secs=_int_env("UPSTREAM_COST_SYNC_TIMEOUT_SECS", 20, 3, 120),
        page_size=_int_env("UPSTREAM_COST_SYNC_PAGE_SIZE", 200, 1, 200),
        lease_secs=_int_env("UPSTREAM_COST_SYNC_LEASE_SECS", 300, 60, 1800),
    )


def _usage_credentials() -> list[str]:
    relay = config.get_openai_relay_settings()
    values = [
        os.getenv("UPSTREAM_COST_SYNC_API_KEY") or "",
        relay.get("api_key") or "",
        *(relay.get("api_keys") if isinstance(relay.get("api_keys"), list) else []),
    ]
    credentials: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = str(value or "").strip()
        if key and key not in seen:
            credentials.append(key)
            seen.add(key)
    return credentials


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        clean_value = str(value or "").strip()
        if not clean_value:
            return None
        try:
            parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _money(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal(0)
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return Decimal(0)
        with localcontext() as context:
            context.prec = 42
            return amount.quantize(MONEY_QUANTUM)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _number(value: object) -> float:
    return float(value or 0)


def _format_datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _public_sync_state(row: UpstreamUsageSyncState | None, settings: UpstreamUsageSettings, configured: bool) -> dict[str, Any]:
    return {
        "provider": PROVIDER,
        "enabled": settings.enabled,
        "configured": configured,
        "scope": settings.scope,
        "status": row.status if row is not None else "idle",
        "last_started_at": _format_datetime(row.last_started_at if row is not None else None),
        "last_success_at": _format_datetime(row.last_success_at if row is not None else None),
        "last_error_at": _format_datetime(row.last_error_at if row is not None else None),
        "last_error": row.last_error if row is not None else "",
        "last_window_from": _format_datetime(row.last_window_from if row is not None else None),
        "last_window_to": _format_datetime(row.last_window_to if row is not None else None),
        "last_full_sync_at": _format_datetime(row.last_full_sync_at if row is not None else None),
        "records_seen": int(row.records_seen or 0) if row is not None else 0,
        "records_upserted": int(row.records_upserted or 0) if row is not None else 0,
        "interval_secs": settings.interval_secs,
        "lookback_days": settings.lookback_days,
        "full_lookback_days": settings.full_lookback_days,
    }


class UpstreamUsageService:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        settings_factory: Callable[[], UpstreamUsageSettings] = upstream_usage_settings,
        credential_loader: Callable[[], list[str]] = _usage_credentials,
        http_get: Callable[..., Any] = requests.get,
        initialize_schema: bool = False,
    ) -> None:
        self.database_url = database_url or resolve_enterprise_database_url()
        self.engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._settings_factory = settings_factory
        self._credential_loader = credential_loader
        self._http_get = http_get
        self._local_lock = threading.Lock()
        if initialize_schema:
            BillingBase.metadata.create_all(self.engine)
            self._ensure_state_row()

    def close(self) -> None:
        self.engine.dispose()

    def _ensure_state_row(self) -> None:
        with self.Session.begin() as session:
            if session.get(UpstreamUsageSyncState, PROVIDER) is None:
                session.add(UpstreamUsageSyncState(provider=PROVIDER, status="idle", updated_at=datetime.now()))

    def status(self) -> dict[str, Any]:
        settings = self._settings_factory()
        configured = bool(settings.usage_url and self._credential_loader())
        try:
            with self.Session() as session:
                row = session.get(UpstreamUsageSyncState, PROVIDER)
                return _public_sync_state(row, settings, configured)
        except Exception as exc:
            result = _public_sync_state(None, settings, configured)
            result.update({"status": "unavailable", "last_error": f"费用账本不可用: {str(exc)[:200]}"})
            return result

    def _acquire_lease(self, lease_owner: str, settings: UpstreamUsageSettings) -> bool:
        now = datetime.now()
        with self.Session.begin() as session:
            row = session.scalars(
                select(UpstreamUsageSyncState)
                .where(UpstreamUsageSyncState.provider == PROVIDER)
                .with_for_update()
            ).one_or_none()
            if row is None:
                row = UpstreamUsageSyncState(provider=PROVIDER, status="idle", updated_at=now)
                session.add(row)
                session.flush()
            if row.lease_owner and row.lease_expires_at and row.lease_expires_at > now:
                return False
            row.lease_owner = lease_owner
            row.lease_expires_at = now + timedelta(seconds=settings.lease_secs)
            row.status = "running"
            row.last_started_at = now
            row.last_error = ""
            row.updated_at = now
        return True

    def _extend_lease(self, lease_owner: str, settings: UpstreamUsageSettings) -> None:
        with self.Session.begin() as session:
            row = session.get(UpstreamUsageSyncState, PROVIDER)
            if row is not None and row.lease_owner == lease_owner:
                row.lease_expires_at = datetime.now() + timedelta(seconds=settings.lease_secs)
                row.updated_at = datetime.now()

    def _finish_sync(
        self,
        lease_owner: str,
        *,
        records_seen: int,
        records_upserted: int,
        window_from: datetime | None,
        window_to: datetime | None,
        full_sync: bool,
    ) -> None:
        now = datetime.now()
        with self.Session.begin() as session:
            row = session.get(UpstreamUsageSyncState, PROVIDER)
            if row is None or row.lease_owner != lease_owner:
                return
            row.status = "success"
            row.last_success_at = now
            row.last_error = ""
            row.last_window_from = window_from
            row.last_window_to = window_to
            if full_sync:
                row.last_full_sync_at = now
            row.records_seen = records_seen
            row.records_upserted = records_upserted
            row.lease_owner = ""
            row.lease_expires_at = None
            row.updated_at = now

    def _fail_sync(self, lease_owner: str, error: Exception) -> None:
        now = datetime.now()
        message = _clean(error, "上游费用同步失败", 1000)
        with self.Session.begin() as session:
            row = session.get(UpstreamUsageSyncState, PROVIDER)
            if row is None or row.lease_owner != lease_owner:
                return
            row.status = "error"
            row.last_error_at = now
            row.last_error = message
            row.lease_owner = ""
            row.lease_expires_at = None
            row.updated_at = now

    def _request_page(
        self,
        settings: UpstreamUsageSettings,
        api_key: str,
        *,
        days: int,
        offset: int,
    ) -> dict[str, Any]:
        response = self._http_get(
            settings.usage_url,
            params={
                "scope": settings.scope,
                "days": days,
                "detail": 1,
                "limit": settings.page_size,
                "offset": offset,
            },
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=settings.timeout_secs,
            **proxy_settings.build_session_kwargs(),
        )
        status_code = int(getattr(response, "status_code", 500) or 500)
        if status_code < 200 or status_code >= 300:
            error = UpstreamUsageError(f"上游费用接口请求失败 (HTTP {status_code})")
            setattr(error, "status_code", status_code)
            raise error
        try:
            payload = response.json()
        except Exception as exc:
            raise UpstreamUsageError("上游费用接口返回了无效 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise UpstreamUsageError("上游费用接口缺少 records 字段")
        return payload

    def _first_page(
        self,
        settings: UpstreamUsageSettings,
        credentials: list[str],
        *,
        days: int,
    ) -> tuple[dict[str, Any], str]:
        last_error: Exception | None = None
        for api_key in credentials:
            try:
                return self._request_page(settings, api_key, days=days, offset=0), api_key
            except UpstreamUsageError as exc:
                last_error = exc
                if getattr(exc, "status_code", 0) not in {401, 403}:
                    break
        raise last_error or UpstreamUsageError("上游费用接口请求失败")

    def _local_associations(
        self,
        upstream_ids: list[str],
    ) -> dict[tuple[str, str], tuple[str, str, str, str]]:
        if not upstream_ids:
            return {}
        tables = set(inspect(self.engine).get_table_names())
        associations: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        placeholders = ", ".join(f":task_{index}" for index in range(len(upstream_ids)))
        params = {f"task_{index}": task_id for index, task_id in enumerate(upstream_ids)}
        with self.engine.connect() as connection:
            if "generation_task_events" in tables:
                image_columns = {column["name"] for column in inspect(self.engine).get_columns("generation_task_events")}
                image_model = "model" if "model" in image_columns else "'' AS model"
                rows = connection.execute(
                    text(
                        f"SELECT upstream_task_id, task_id, owner_id, {image_model} FROM generation_task_events "
                        f"WHERE upstream_task_id IN ({placeholders}) "
                        "ORDER BY task_updated_at DESC, id DESC"
                    ),
                    params,
                ).mappings()
                for row in rows:
                    upstream_id = _clean(row.get("upstream_task_id"), limit=255)
                    requested_model = _clean(row.get("model"), limit=191)
                    associations.setdefault(
                        ("image", upstream_id),
                        (
                            _clean(row.get("owner_id")),
                            _clean(row.get("task_id")),
                            requested_model,
                            _model_version(requested_model),
                        ),
                    )
            if "video_generation_records" in tables:
                video_columns = {column["name"] for column in inspect(self.engine).get_columns("video_generation_records")}
                video_model = "model" if "model" in video_columns else "'' AS model"
                rows = connection.execute(
                    text(
                        f"SELECT upstream_task_id, task_id, owner_id, {video_model} FROM video_generation_records "
                        f"WHERE upstream_task_id IN ({placeholders}) "
                        "ORDER BY event_at DESC, task_key DESC"
                    ),
                    params,
                ).mappings()
                for row in rows:
                    upstream_id = _clean(row.get("upstream_task_id"), limit=255)
                    requested_model = _clean(row.get("model"), limit=191)
                    associations.setdefault(
                        ("video", upstream_id),
                        (
                            _clean(row.get("owner_id")),
                            _clean(row.get("task_id")),
                            requested_model,
                            _model_version(requested_model),
                        ),
                    )
            if "audio_generation_records" in tables:
                audio_columns = {column["name"] for column in inspect(self.engine).get_columns("audio_generation_records")}
                audio_model = "model" if "model" in audio_columns else "'' AS model"
                rows = connection.execute(
                    text(
                        f"SELECT upstream_task_id, task_id, owner_id, {audio_model} FROM audio_generation_records "
                        f"WHERE upstream_task_id IN ({placeholders}) "
                        "ORDER BY event_at DESC, task_key DESC"
                    ),
                    params,
                ).mappings()
                for row in rows:
                    upstream_id = _clean(row.get("upstream_task_id"), limit=255)
                    requested_model = _clean(row.get("model"), limit=191)
                    associations.setdefault(
                        ("audio", upstream_id),
                        (
                            _clean(row.get("owner_id")),
                            _clean(row.get("task_id")),
                            requested_model,
                            _model_version(requested_model),
                        ),
                    )
            if "upstream_usage_attributions" in tables:
                rows = connection.execute(
                    text(
                        "SELECT upstream_task_id, upstream_request_id, local_task_id, owner_id, requested_model, model_version "
                        "FROM upstream_usage_attributions "
                        f"WHERE provider = :provider AND (upstream_task_id IN ({placeholders}) "
                        f"OR upstream_request_id IN ({placeholders.replace('task_', 'request_')})) "
                        "ORDER BY updated_at DESC, id DESC"
                    ),
                    {
                        **params,
                        **{f"request_{index}": task_id for index, task_id in enumerate(upstream_ids)},
                        "provider": PROVIDER,
                    },
                ).mappings()
                for row in rows:
                    upstream_task_id = _clean(row.get("upstream_task_id"), limit=255)
                    upstream_request_id = _clean(row.get("upstream_request_id"), limit=255)
                    if not upstream_task_id and not upstream_request_id:
                        continue
                    association = (
                        _clean(row.get("owner_id")),
                        _clean(row.get("local_task_id")),
                        _clean(row.get("requested_model")),
                        _clean(row.get("model_version"), limit=64),
                    )
                    if upstream_task_id:
                        associations.setdefault(("chat", upstream_task_id), association)
                    if upstream_request_id:
                        associations.setdefault(("chat", upstream_request_id), association)
        return associations

    def _store_records(self, records: list[dict[str, Any]], *, unit: str, key_fingerprint: str) -> int:
        normalized = [record for record in records if isinstance(record, dict) and _clean(record.get("task_id"), limit=255)]
        if not normalized:
            return 0
        upstream_ids = [_clean(record.get("task_id"), limit=255) for record in normalized]
        associations = self._local_associations(upstream_ids)
        now = datetime.now()
        with self.Session.begin() as session:
            existing = {
                row.upstream_task_id: row
                for row in session.scalars(
                    select(UpstreamUsageRecord).where(
                        UpstreamUsageRecord.provider == PROVIDER,
                        UpstreamUsageRecord.upstream_task_id.in_(upstream_ids),
                    )
                )
            }
            for item in normalized:
                upstream_id = _clean(item.get("task_id"), limit=255)
                model_type = _clean(item.get("model_type"), "unknown", 32).lower()
                if model_type not in MODEL_TYPES:
                    model_type = "unknown"
                created_at = _parse_datetime(item.get("created_at"))
                completed_at = _parse_datetime(item.get("completed_at"))
                row = existing.get(upstream_id)
                if row is None:
                    row = UpstreamUsageRecord(
                        provider=PROVIDER,
                        upstream_task_id=upstream_id,
                        first_seen_at=now,
                        event_at=completed_at or created_at or now,
                    )
                    session.add(row)
                    existing[upstream_id] = row
                row.model = _clean(item.get("model"), "unknown")
                upstream_model_version = _usage_model_version(item)
                if upstream_model_version:
                    row.model_version = upstream_model_version
                row.model_type = model_type
                row.channel_group = _clean(item.get("channel_group"))
                row.state = _clean(item.get("state"), "unknown", 32).lower()
                row.unit = _clean(unit, limit=32)
                row.cost = _money(item.get("cost"))
                row.refunded = _bool(item.get("refunded"))
                row.refunded_amount = _money(item.get("refunded_amount"))
                row.upstream_created_at = created_at
                row.upstream_completed_at = completed_at
                row.event_at = completed_at or created_at or row.event_at or now
                row.sync_key_fingerprint = key_fingerprint
                association = associations.get((model_type, upstream_id))
                if association:
                    owner_id, local_task_id, requested_model, local_model_version = association
                    row.owner_id = owner_id or row.owner_id
                    row.local_task_id = local_task_id
                    if requested_model:
                        row.requested_model = requested_model
                    row.model_version = local_model_version or row.model_version
                    row.local_source = model_type
                row.updated_at = now
        return len(normalized)

    def sync_usage(self, *, days: int | None = None, full_sync: bool = False) -> dict[str, Any]:
        settings = self._settings_factory()
        credentials = self._credential_loader()
        if not settings.enabled:
            raise UpstreamUsageNotConfigured("上游费用同步未启用")
        if not settings.usage_url or not credentials:
            raise UpstreamUsageNotConfigured("上游费用同步缺少接口地址或 API Key")
        sync_days = max(1, min(30, int(days or settings.lookback_days)))
        lease_owner = uuid.uuid4().hex
        with self._local_lock:
            if not self._acquire_lease(lease_owner, settings):
                raise UpstreamUsageSyncInProgress("上游费用正在同步，请稍后再试")
        records_seen = 0
        records_upserted = 0
        unique_task_ids: set[str] = set()
        window_from: datetime | None = None
        window_to: datetime | None = None
        try:
            payload, api_key = self._first_page(settings, credentials, days=sync_days)
            offset = 0
            key_fingerprint = _fingerprint(api_key)
            while True:
                records = payload.get("records") or []
                unit = _clean(payload.get("unit"), limit=32)
                records_seen += len(records)
                self._store_records(records, unit=unit, key_fingerprint=key_fingerprint)
                unique_task_ids.update(
                    _clean(record.get("task_id"), limit=255)
                    for record in records
                    if isinstance(record, dict) and _clean(record.get("task_id"), limit=255)
                )
                records_upserted = len(unique_task_ids)
                window_from = _parse_datetime(payload.get("from")) or window_from
                window_to = _parse_datetime(payload.get("to")) or window_to
                total = max(0, int(payload.get("total") or 0))
                offset += len(records)
                if not records or offset >= total:
                    break
                self._extend_lease(lease_owner, settings)
                payload = self._request_page(settings, api_key, days=sync_days, offset=offset)
            self._finish_sync(
                lease_owner,
                records_seen=records_seen,
                records_upserted=records_upserted,
                window_from=window_from,
                window_to=window_to,
                full_sync=full_sync,
            )
        except Exception as exc:
            self._fail_sync(lease_owner, exc)
            raise
        result = {
            "ok": True,
            "provider": PROVIDER,
            "scope": settings.scope,
            "days": sync_days,
            "records_seen": records_seen,
            "records_upserted": records_upserted,
            "window_from": _format_datetime(window_from),
            "window_to": _format_datetime(window_to),
        }
        logger.info({"event": "upstream_usage_sync_complete", **result})
        return result

    @staticmethod
    def _source_filter(source: str):
        normalized = source if source in MODEL_TYPES else "all"
        return normalized, None if normalized == "all" else UpstreamUsageRecord.model_type == normalized

    @staticmethod
    def _range_filters(start_at: datetime | None, end_at: datetime | None) -> list[Any]:
        filters: list[Any] = []
        if start_at is not None:
            filters.append(UpstreamUsageRecord.event_at >= start_at)
        if end_at is not None:
            filters.append(UpstreamUsageRecord.event_at < end_at)
        return filters

    @staticmethod
    def _user_map() -> dict[str, dict[str, Any]]:
        try:
            users = user_service.list_users().get("items") or []
        except Exception:
            return {}
        return {_clean(user.get("id")): user for user in users if isinstance(user, dict) and _clean(user.get("id"))}

    def summary(
        self,
        *,
        source: str = "all",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_source, source_filter = self._source_filter(source)
        filters = self._range_filters(start_at, end_at)
        if source_filter is not None:
            filters.append(source_filter)
        record = UpstreamUsageRecord
        success_case = case((record.state.in_(SUCCESS_STATES), 1), else_=0)
        failed_case = case((record.state.in_(FAILED_STATES), 1), else_=0)
        refunded_case = case((record.refunded.is_(True), 1), else_=0)
        with self.Session() as session:
            totals = session.execute(
                select(
                    func.count(record.id),
                    func.coalesce(func.sum(success_case), 0),
                    func.coalesce(func.sum(failed_case), 0),
                    func.coalesce(func.sum(record.cost), 0),
                    func.coalesce(func.sum(refunded_case), 0),
                    func.coalesce(func.sum(record.refunded_amount), 0),
                    func.coalesce(func.sum(case((record.owner_id.is_(None), 1), else_=0)), 0),
                ).where(*filters)
            ).one()
            model_rows = session.execute(
                select(
                    record.requested_model,
                    record.model,
                    record.model_version,
                    record.model_type,
                    func.count(record.id),
                    func.coalesce(func.sum(success_case), 0),
                    func.coalesce(func.sum(failed_case), 0),
                    func.coalesce(func.sum(record.cost), 0),
                    func.coalesce(func.sum(refunded_case), 0),
                    func.coalesce(func.sum(record.refunded_amount), 0),
                ).where(*filters).group_by(
                    record.requested_model,
                    record.model,
                    record.model_version,
                    record.model_type,
                )
            ).all()
            owner_rows = session.execute(
                select(
                    record.owner_id,
                    func.count(record.id),
                    func.coalesce(func.sum(record.cost), 0),
                    func.coalesce(func.sum(refunded_case), 0),
                    func.coalesce(func.sum(record.refunded_amount), 0),
                ).where(*filters).group_by(record.owner_id)
            ).all()
            units = [row[0] for row in session.execute(select(record.unit).where(*filters).distinct()).all() if row[0]]
        users = self._user_map()
        model_totals: dict[tuple[str, str], dict[str, Any]] = {}
        for row in model_rows:
            model_name = _display_model(row[1], row[0], row[2], row[3])
            model_type = row[3]
            key = (model_name, model_type)
            item = model_totals.setdefault(key, {
                "model": model_name,
                "model_version": _clean(row[2], limit=64) or _model_version(model_name),
                "model_type": model_type,
                "count": 0,
                "success_count": 0,
                "failed_count": 0,
                "cost": 0.0,
                "refunded_count": 0,
                "refunded_amount": 0.0,
            })
            item["count"] += int(row[4] or 0)
            item["success_count"] += int(row[5] or 0)
            item["failed_count"] += int(row[6] or 0)
            item["cost"] += _number(row[7])
            item["refunded_count"] += int(row[8] or 0)
            item["refunded_amount"] += _number(row[9])
        model_items = list(model_totals.values())
        model_items.sort(key=lambda item: (-item["cost"], item["model"]))
        user_items = []
        for owner_id, count, cost, refunded_count, refunded_amount in owner_rows:
            user = users.get(owner_id or "", {})
            user_items.append(
                {
                    "owner_id": owner_id or "",
                    "username": user.get("username") or owner_id or "",
                    "name": user.get("name") or user.get("username") or owner_id or "未归属",
                    "count": int(count or 0),
                    "cost": _number(cost),
                    "refunded_count": int(refunded_count or 0),
                    "refunded_amount": _number(refunded_amount),
                }
            )
        user_items.sort(key=lambda item: (-item["cost"], item["name"]))
        record_count = int(totals[0] or 0)
        unassigned_count = int(totals[6] or 0)
        unit = units[0] if len(units) == 1 else ("mixed" if units else "")
        return {
            "source": normalized_source,
            "unit": unit,
            "record_count": record_count,
            "success_count": int(totals[1] or 0),
            "failed_count": int(totals[2] or 0),
            "total_cost": _number(totals[3]),
            "refunded_count": int(totals[4] or 0),
            "refunded_amount": _number(totals[5]),
            "assigned_count": record_count - unassigned_count,
            "unassigned_count": unassigned_count,
            "models": model_items,
            "users": user_items,
            "range": {
                "start_at": _format_datetime(start_at),
                "end_at": _format_datetime(end_at),
                "end_exclusive": True,
            },
            "sync": self.status(),
        }

    def list_records(
        self,
        *,
        source: str = "all",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        query_text: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        normalized_source, source_filter = self._source_filter(source)
        filters = self._range_filters(start_at, end_at)
        if source_filter is not None:
            filters.append(source_filter)
        query = _clean(query_text, limit=191).lower()
        users = self._user_map()
        if query:
            matching_owner_ids = [
                owner_id
                for owner_id, user in users.items()
                if query in str(user.get("username") or "").lower()
                or query in str(user.get("name") or "").lower()
            ]
            pattern = f"%{query}%"
            query_filters = [
                func.lower(UpstreamUsageRecord.upstream_task_id).like(pattern),
                func.lower(UpstreamUsageRecord.model).like(pattern),
                func.lower(UpstreamUsageRecord.requested_model).like(pattern),
                func.lower(UpstreamUsageRecord.model_version).like(pattern),
                func.lower(UpstreamUsageRecord.model_type).like(pattern),
                func.lower(UpstreamUsageRecord.channel_group).like(pattern),
                func.lower(UpstreamUsageRecord.local_task_id).like(pattern),
                func.lower(func.coalesce(UpstreamUsageRecord.owner_id, "")).like(pattern),
            ]
            if matching_owner_ids:
                query_filters.append(UpstreamUsageRecord.owner_id.in_(matching_owner_ids))
            filters.append(or_(*query_filters))
        safe_limit = max(1, min(200, int(limit or 20)))
        safe_offset = max(0, int(offset or 0))
        with self.Session() as session:
            total, cost_total, refund_total = session.execute(
                select(
                    func.count(UpstreamUsageRecord.id),
                    func.coalesce(func.sum(UpstreamUsageRecord.cost), 0),
                    func.coalesce(func.sum(UpstreamUsageRecord.refunded_amount), 0),
                ).where(*filters)
            ).one()
            rows = session.scalars(
                select(UpstreamUsageRecord)
                .where(*filters)
                .order_by(UpstreamUsageRecord.event_at.desc(), UpstreamUsageRecord.id.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            ).all()
        items = []
        for row in rows:
            user = users.get(row.owner_id or "", {})
            items.append(
                {
                    "row_key": f"upstream:{row.id}",
                    "upstream_task_id": row.upstream_task_id,
                    "model": _display_model(row.model, row.requested_model, row.model_version, row.model_type),
                    "upstream_model": row.model,
                    "requested_model": row.requested_model,
                    "model_version": row.model_version,
                    "model_type": row.model_type,
                    "channel_group": row.channel_group,
                    "state": row.state,
                    "unit": row.unit,
                    "cost": _number(row.cost),
                    "refunded": bool(row.refunded),
                    "refunded_amount": _number(row.refunded_amount),
                    "created_at": _format_datetime(row.upstream_created_at),
                    "completed_at": _format_datetime(row.upstream_completed_at),
                    "event_at": _format_datetime(row.event_at),
                    "local_source": row.local_source,
                    "local_task_id": row.local_task_id,
                    "owner_id": row.owner_id or "",
                    "owner_username": user.get("username") or row.owner_id or "",
                    "owner_name": user.get("name") or user.get("username") or row.owner_id or "未归属",
                    "attribution_status": "matched" if row.owner_id else "unassigned",
                }
            )
        units = sorted({item["unit"] for item in items if item["unit"]})
        return {
            "source": normalized_source,
            "items": items,
            "record_count": int(total or 0),
            "cost_total": _number(cost_total),
            "refunded_amount": _number(refund_total),
            "unit": units[0] if len(units) == 1 else ("mixed" if units else ""),
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < int(total or 0),
            "query": query,
            "range": {
                "start_at": _format_datetime(start_at),
                "end_at": _format_datetime(end_at),
                "end_exclusive": True,
            },
        }


upstream_usage_service = UpstreamUsageService()


def _scheduler_worker(stop_event: threading.Event, service: UpstreamUsageService) -> None:
    settings = service._settings_factory()
    if not settings.enabled:
        return
    while not stop_event.is_set():
        try:
            status = service.status()
            last_full_sync = _parse_datetime(status.get("last_full_sync_at"))
            full_sync = last_full_sync is None or datetime.now() - last_full_sync >= timedelta(hours=24)
            days = settings.full_lookback_days if full_sync else settings.lookback_days
            service.sync_usage(days=days, full_sync=full_sync)
        except UpstreamUsageSyncInProgress:
            pass
        except Exception as exc:
            logger.info({"event": "upstream_usage_sync_failed", "error": _clean(exc, limit=500)})
        if stop_event.wait(settings.interval_secs):
            break
        settings = service._settings_factory()
        if not settings.enabled:
            break


def start_upstream_usage_scheduler(
    stop_event: threading.Event,
    service: UpstreamUsageService = upstream_usage_service,
) -> threading.Thread:
    thread = threading.Thread(
        target=_scheduler_worker,
        args=(stop_event, service),
        daemon=True,
        name="upstream-usage-sync",
    )
    thread.start()
    return thread
