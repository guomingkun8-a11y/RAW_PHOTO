from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from uuid import uuid4

from minio import Minio
from minio.error import S3Error
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint, desc
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.ecommerce.ecommerce_agent_memory_service import Base
from services.platform.config import config
from services.platform.enterprise_schema import resolve_enterprise_database_url


LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")
VIDEO_MAX_ITEMS = 4
VIDEO_MAX_FILE_BYTES = 300 * 1024 * 1024
VIDEO_MAX_TOTAL_BYTES = 600 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
logger = logging.getLogger(__name__)


class ProfessionalVideoAssetModel(Base):
    __tablename__ = "professional_agent_video_assets"
    __table_args__ = (UniqueConstraint("owner_id", "sha256", name="uq_prof_agent_video_owner_sha"),)

    video_id = Column(String(191), primary_key=True)
    owner_id = Column(String(191), nullable=False)
    conversation_id = Column(String(191), nullable=False, default="")
    filename = Column(String(191), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    sha256 = Column(String(64), nullable=False)
    storage_provider = Column(String(32), nullable=False, default="oss")
    bucket = Column(String(191), nullable=False, default="")
    object_key = Column(String(1000), nullable=False, default="")
    url = Column(String(2000), nullable=False, default="")
    status = Column(String(32), nullable=False, default="uploaded")
    analysis_status = Column(String(32), nullable=False, default="pending")
    analysis_json = Column(LONG_TEXT, nullable=False, default="{}")
    analysis_error = Column(LONG_TEXT, nullable=False, default="")
    analysis_started_at = Column(DateTime, nullable=True)
    analysis_finished_at = Column(DateTime, nullable=True)
    analysis_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfessionalVideoUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfessionalVideoUploadResult:
    video_id: str
    url: str
    sha256: str
    filename: str
    mime_type: str
    file_size: int
    cached: bool
    status: str
    analysis_status: str
    conversation_id: str = ""
    analysis_error: str = ""
    analysis: dict[str, Any] | None = None
    analysis_started_at: str = ""
    analysis_finished_at: str = ""
    analysis_version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def to_public(self) -> dict[str, object]:
        result: dict[str, object] = {
            "videoId": self.video_id,
            "conversationId": self.conversation_id,
            "url": self.url,
            "sha256": self.sha256,
            "filename": self.filename,
            "name": self.filename,
            "mimeType": self.mime_type,
            "type": self.mime_type,
            "fileSize": self.file_size,
            "size": self.file_size,
            "cached": self.cached,
            "status": self.status,
            "analysisStatus": self.analysis_status,
            "analysisError": self.analysis_error,
            "analysisStartedAt": self.analysis_started_at,
            "analysisFinishedAt": self.analysis_finished_at,
            "analysisVersion": self.analysis_version,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if self.analysis is not None:
            result["analysis"] = self.analysis
        return result


def _clean(value: object, default: str = "", limit: int = 12000) -> str:
    text = str(value if value is not None else default).strip()
    return (text or default)[:limit]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if parsed is not None else default


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _owner_id(identity: Mapping[str, object] | str | None) -> str:
    if isinstance(identity, str):
        return _clean(identity, "anonymous", 191)
    source = identity or {}
    return _clean(source.get("id") or source.get("username"), "anonymous", 191)


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(_clean(filename, "video.mp4", 240).replace("\\", "/")).name
    name = re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", name).strip(" .")
    return (name or "video.mp4")[:191]


def _safe_extension(filename: str, mime_type: str) -> str:
    suffix = PurePosixPath(_safe_filename(filename)).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return suffix
    mime = _clean(mime_type).lower()
    if "quicktime" in mime:
        return ".mov"
    if "webm" in mime:
        return ".webm"
    if "x-msvideo" in mime:
        return ".avi"
    if "matroska" in mime:
        return ".mkv"
    return ".mp4"


def _settings() -> dict[str, object]:
    return config.get_video_upload_settings()


def _public_base_url(item: dict[str, object] | None = None) -> str:
    settings_item = item or _settings()
    explicit = _clean(settings_item.get("public_base_url")).rstrip("/")
    if explicit:
        return explicit
    endpoint = _clean(settings_item.get("oss_endpoint")).rstrip("/")
    bucket = _clean(settings_item.get("oss_bucket"))
    if not endpoint or not bucket:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{bucket}.{parsed.netloc}".rstrip("/")
    return ""


def is_enabled() -> bool:
    item = _settings()
    return bool(
        item.get("enabled")
        and _clean(item.get("oss_endpoint"))
        and _clean(item.get("oss_access_key"))
        and _clean(item.get("oss_secret_key"))
        and _clean(item.get("oss_bucket"))
        and _public_base_url(item)
    )


def _public_url(item: dict[str, object], object_key: str) -> str:
    public_base_url = _public_base_url(item)
    if not public_base_url:
        raise ProfessionalVideoUploadError("OSS public base URL is empty")
    return f"{public_base_url}/{quote(object_key.lstrip('/'), safe='/')}"


def _object_key(filename: str, digest: str, mime_type: str) -> str:
    item = _settings()
    prefix = _clean(item.get("oss_prefix")).strip("/")
    suffix = _safe_extension(filename, mime_type)
    key = f"sha256/{digest[:2]}/{digest}{suffix}"
    return f"{prefix}/{key}" if prefix else key


def _oss_client(item: dict[str, object]) -> Minio:
    endpoint = _clean(item.get("oss_endpoint")).rstrip("/")
    access_key = _clean(item.get("oss_access_key"))
    secret_key = _clean(item.get("oss_secret_key"))
    region = _clean(item.get("oss_region")) or None
    if not endpoint or not access_key or not secret_key:
        raise ProfessionalVideoUploadError("OSS video upload is not configured")
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"}:
        endpoint = parsed.netloc
        secure = parsed.scheme == "https"
    else:
        secure = _bool(item.get("oss_secure"), True)
    if not endpoint:
        raise ProfessionalVideoUploadError("invalid OSS endpoint")
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=region)


class ProfessionalVideoAssetService:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url
        self.engine = None
        self.Session = None

    def _session(self):
        if self.Session is None:
            self.engine = create_engine(
                resolve_enterprise_database_url(self.database_url),
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            Base.metadata.create_all(self.engine)
            self.Session = sessionmaker(bind=self.engine)
        return self.Session()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.Session = None

    @staticmethod
    def _row_to_result(row: ProfessionalVideoAssetModel, *, cached: bool) -> ProfessionalVideoUploadResult:
        analysis_status = _clean(row.analysis_status, "pending", 32)
        parsed_analysis = _load(row.analysis_json, {}) if analysis_status == "ready" else None
        return ProfessionalVideoUploadResult(
            video_id=row.video_id,
            url=row.url,
            sha256=row.sha256,
            filename=row.filename,
            mime_type=row.mime_type,
            file_size=int(row.size or 0),
            cached=cached,
            status=row.status,
            analysis_status=analysis_status,
            conversation_id=row.conversation_id,
            analysis_error=_clean(getattr(row, "analysis_error", ""), limit=4000),
            analysis=parsed_analysis if isinstance(parsed_analysis, dict) else None,
            analysis_started_at=_iso(getattr(row, "analysis_started_at", None)),
            analysis_finished_at=_iso(getattr(row, "analysis_finished_at", None)),
            analysis_version=int(getattr(row, "analysis_version", 1) or 1),
            created_at=_iso(row.created_at),
            updated_at=_iso(row.updated_at),
        )

    @staticmethod
    def _row_to_public(row: ProfessionalVideoAssetModel) -> dict[str, object]:
        return ProfessionalVideoAssetService._row_to_result(row, cached=False).to_public()

    @staticmethod
    def _row_to_internal(row: ProfessionalVideoAssetModel) -> dict[str, Any]:
        item = ProfessionalVideoAssetService._row_to_public(row)
        item.update({
            "ownerId": row.owner_id,
            "conversationId": row.conversation_id,
            "storageProvider": row.storage_provider,
            "bucket": row.bucket,
            "objectKey": row.object_key,
        })
        return item

    def _existing_by_digest(self, *, owner_id: str, digest: str) -> ProfessionalVideoUploadResult | None:
        session = self._session()
        try:
            row = session.query(ProfessionalVideoAssetModel).filter(
                ProfessionalVideoAssetModel.owner_id == owner_id,
                ProfessionalVideoAssetModel.sha256 == digest,
                ProfessionalVideoAssetModel.status != "deleted",
            ).order_by(desc(ProfessionalVideoAssetModel.updated_at)).first()
            if row is None:
                return None
            row.updated_at = datetime.now()
            session.commit()
            return self._row_to_result(row, cached=True)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upload_one(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        payload: bytes,
        filename: str,
        mime_type: str,
    ) -> ProfessionalVideoUploadResult:
        owner = _owner_id(owner_id)
        safe_filename = _safe_filename(filename)
        suffix = _safe_extension(safe_filename, mime_type)
        safe_mime = _clean(mime_type, "video/mp4", 120)
        if not payload:
            raise ProfessionalVideoUploadError(f"{safe_filename} is empty")
        if len(payload) > VIDEO_MAX_FILE_BYTES:
            raise ProfessionalVideoUploadError(f"{safe_filename} exceeds the 300MB file limit")
        if not safe_mime.startswith("video/") and suffix not in VIDEO_EXTENSIONS:
            raise ProfessionalVideoUploadError(f"{safe_filename} is not a supported video file")
        if not is_enabled():
            raise ProfessionalVideoUploadError("OSS video upload is not configured")

        digest = hashlib.sha256(payload).hexdigest()
        existing = self._existing_by_digest(owner_id=owner, digest=digest)
        if existing is not None:
            return existing

        item = _settings()
        bucket = _clean(item.get("oss_bucket"))
        object_key = _object_key(safe_filename, digest, safe_mime)
        client = _oss_client(item)
        try:
            try:
                client.stat_object(bucket, object_key)
            except S3Error as exc:
                if exc.code not in {"NoSuchKey", "NoSuchBucket", "NoSuchObject", "NotFound"}:
                    raise
                client.put_object(
                    bucket,
                    object_key,
                    BytesIO(payload),
                    length=len(payload),
                    content_type=safe_mime,
                    part_size=10 * 1024 * 1024,
                    num_parallel_uploads=1,
                )
        except Exception as exc:
            message = str(exc)[:300] or exc.__class__.__name__
            raise ProfessionalVideoUploadError(f"OSS video upload failed: {message}") from exc

        url = _public_url(item, object_key)
        session = self._session()
        try:
            row = ProfessionalVideoAssetModel(
                video_id=f"video-{uuid4().hex}",
                owner_id=owner,
                conversation_id=_clean(conversation_id, limit=191),
                filename=safe_filename,
                mime_type=safe_mime,
                size=len(payload),
                sha256=digest,
                storage_provider="oss",
                bucket=bucket,
                object_key=object_key,
                url=url,
                status="uploaded",
                analysis_status="pending",
            )
            session.add(row)
            session.commit()
            return self._row_to_result(row, cached=False)
        except Exception:
            session.rollback()
            existing_after_race = self._existing_by_digest(owner_id=owner, digest=digest)
            if existing_after_race is not None:
                return existing_after_race
            raise
        finally:
            session.close()

    def upload_many(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        videos: list[tuple[bytes, str, str]],
    ) -> list[ProfessionalVideoUploadResult]:
        if not videos:
            return []
        if len(videos) > VIDEO_MAX_ITEMS:
            raise ProfessionalVideoUploadError(f"video count must be between 1 and {VIDEO_MAX_ITEMS}")
        total = sum(len(payload) for payload, _filename, _mime_type in videos)
        if total > VIDEO_MAX_TOTAL_BYTES:
            raise ProfessionalVideoUploadError("video batch exceeds the 600MB total size limit")
        return [
            self.upload_one(
                owner_id=owner_id,
                conversation_id=conversation_id,
                payload=payload,
                filename=filename,
                mime_type=mime_type,
            )
            for payload, filename, mime_type in videos
        ]

    def get_video(self, video_id: str, *, owner_id: str, include_internal: bool = False) -> dict[str, Any] | None:
        owner = _owner_id(owner_id)
        session = self._session()
        try:
            row = session.query(ProfessionalVideoAssetModel).filter(
                ProfessionalVideoAssetModel.video_id == _clean(video_id, limit=191),
                ProfessionalVideoAssetModel.owner_id == owner,
                ProfessionalVideoAssetModel.status != "deleted",
            ).one_or_none()
            if row is None:
                return None
            return self._row_to_internal(row) if include_internal else self._row_to_public(row)
        finally:
            session.close()

    def list_videos(self, video_ids: list[str], *, owner_id: str) -> list[dict[str, object]]:
        owner = _owner_id(owner_id)
        ids = list(dict.fromkeys(_clean(item, limit=191) for item in video_ids if _clean(item, limit=191)))[:50]
        if not ids:
            return []
        session = self._session()
        try:
            rows = session.query(ProfessionalVideoAssetModel).filter(
                ProfessionalVideoAssetModel.video_id.in_(ids),
                ProfessionalVideoAssetModel.owner_id == owner,
                ProfessionalVideoAssetModel.status != "deleted",
            ).all()
            by_id = {row.video_id: row for row in rows}
            return [self._row_to_public(by_id[video_id]) for video_id in ids if video_id in by_id]
        finally:
            session.close()

    def delete_video(
        self,
        video_id: str,
        *,
        owner_id: str,
        conversation_id: str = "",
    ) -> bool:
        owner = _owner_id(owner_id)
        clean_video_id = _clean(video_id, limit=191)
        expected_conversation_id = _clean(conversation_id, limit=191)
        if not clean_video_id:
            return False

        session = self._session()
        storage_provider = ""
        bucket = ""
        object_key = ""
        try:
            row = (
                session.query(ProfessionalVideoAssetModel)
                .filter(
                    ProfessionalVideoAssetModel.video_id == clean_video_id,
                    ProfessionalVideoAssetModel.owner_id == owner,
                    ProfessionalVideoAssetModel.status != "deleted",
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return False
            if expected_conversation_id and row.conversation_id != expected_conversation_id:
                return False
            storage_provider = _clean(row.storage_provider, "oss", 32)
            bucket = _clean(row.bucket, limit=191)
            object_key = _clean(row.object_key, limit=1000)
            session.delete(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        if storage_provider == "oss" and bucket and object_key:
            reference_session = self._session()
            try:
                remaining_references = reference_session.query(ProfessionalVideoAssetModel).filter(
                    ProfessionalVideoAssetModel.storage_provider == storage_provider,
                    ProfessionalVideoAssetModel.bucket == bucket,
                    ProfessionalVideoAssetModel.object_key == object_key,
                    ProfessionalVideoAssetModel.status != "deleted",
                ).count()
            finally:
                reference_session.close()
            if not remaining_references:
                try:
                    _oss_client(_settings()).remove_object(bucket, object_key)
                except S3Error as exc:
                    if exc.code not in {"NoSuchBucket", "NoSuchKey", "NoSuchObject", "NotFound"}:
                        logger.warning("Could not delete video object %s/%s: %s", bucket, object_key, exc)
                except Exception as exc:
                    logger.warning("Could not delete video object %s/%s: %s", bucket, object_key, exc)
        return True

    def mark_analysis_queued(self, video_id: str, *, owner_id: str, error: str = "") -> dict[str, object] | None:
        return self._update_analysis_state(
            video_id,
            owner_id=owner_id,
            analysis_status="queued",
            analysis_error=error,
            clear_finished=True,
        )

    def mark_analysis_processing(self, video_id: str, *, owner_id: str) -> dict[str, object] | None:
        return self._update_analysis_state(
            video_id,
            owner_id=owner_id,
            analysis_status="processing",
            analysis_error="",
            started_at=datetime.now(),
            clear_finished=True,
        )

    def mark_analysis_ready(self, video_id: str, *, owner_id: str, analysis: Mapping[str, Any]) -> dict[str, object] | None:
        payload = dict(analysis)
        return self._update_analysis_state(
            video_id,
            owner_id=owner_id,
            analysis_status="ready",
            analysis_json=_dump(payload),
            analysis_error="",
            finished_at=datetime.now(),
            analysis_version=int(payload.get("version") or 1),
        )

    def mark_analysis_failed(
        self,
        video_id: str,
        *,
        owner_id: str,
        error: str,
        partial: Mapping[str, Any] | None = None,
    ) -> dict[str, object] | None:
        payload = dict(partial or {})
        return self._update_analysis_state(
            video_id,
            owner_id=owner_id,
            analysis_status="failed",
            analysis_json=_dump(payload) if payload else None,
            analysis_error=_clean(error, limit=12000),
            finished_at=datetime.now(),
        )

    def mark_analysis_pending(self, video_id: str, *, owner_id: str, error: str = "") -> dict[str, object] | None:
        return self._update_analysis_state(
            video_id,
            owner_id=owner_id,
            analysis_status="pending",
            analysis_error=error,
        )

    def _update_analysis_state(
        self,
        video_id: str,
        *,
        owner_id: str,
        analysis_status: str,
        analysis_json: str | None = None,
        analysis_error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        clear_finished: bool = False,
        analysis_version: int | None = None,
    ) -> dict[str, object] | None:
        owner = _owner_id(owner_id)
        session = self._session()
        try:
            row = session.query(ProfessionalVideoAssetModel).filter(
                ProfessionalVideoAssetModel.video_id == _clean(video_id, limit=191),
                ProfessionalVideoAssetModel.owner_id == owner,
                ProfessionalVideoAssetModel.status != "deleted",
            ).one_or_none()
            if row is None:
                return None
            row.analysis_status = _clean(analysis_status, "pending", 32)
            if analysis_json is not None:
                row.analysis_json = analysis_json
            if analysis_error is not None:
                row.analysis_error = _clean(analysis_error, limit=12000)
            if started_at is not None:
                row.analysis_started_at = started_at
            if finished_at is not None:
                row.analysis_finished_at = finished_at
            elif clear_finished:
                row.analysis_finished_at = None
            if analysis_version is not None:
                row.analysis_version = max(1, int(analysis_version))
            row.updated_at = datetime.now()
            session.commit()
            return self._row_to_public(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def request_analysis(self, video_id: str, *, owner_id: str, force: bool = False) -> dict[str, object] | None:
        settings = config.get_video_analysis_settings()
        current = self.get_video(video_id, owner_id=owner_id)
        if current is None:
            return None
        if not bool(settings.get("enabled") and settings.get("queue_enabled")):
            from services.ecommerce.video_analysis_queue_service import VideoAnalysisQueueUnavailable

            error = "video analysis queue is disabled"
            self.mark_analysis_failed(video_id, owner_id=owner_id, error=error)
            raise VideoAnalysisQueueUnavailable(error)
        status = _clean(current.get("analysisStatus"), "pending", 32).lower()
        if status in {"ready", "processing", "queued"} and not force:
            return current
        queued = self.mark_analysis_queued(video_id, owner_id=owner_id)
        try:
            from services.ecommerce.video_analysis_queue_service import video_analysis_queue_service

            video_analysis_queue_service.enqueue(
                video_id=video_id,
                owner_id=owner_id,
                conversation_id=_clean(current.get("conversationId"), limit=191),
            )
        except Exception as exc:
            self.mark_analysis_failed(video_id, owner_id=owner_id, error=str(exc)[:2000])
            raise
        return queued or self.get_video(video_id, owner_id=owner_id)

    def download_to_path(self, video_id: str, *, owner_id: str, destination: Path) -> dict[str, Any]:
        item = self.get_video(video_id, owner_id=owner_id, include_internal=True)
        if item is None:
            raise KeyError("video asset not found")
        provider = _clean(item.get("storageProvider"), "oss", 32)
        bucket = _clean(item.get("bucket"), limit=191)
        object_key = _clean(item.get("objectKey"), limit=1000)
        if provider == "oss" and bucket and object_key:
            settings = _settings()
            client = _oss_client(settings)
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.fget_object(bucket, object_key, str(destination))
            return item
        url = _clean(item.get("url"), limit=2000)
        if not url:
            raise ProfessionalVideoUploadError("video asset has no downloadable URL")
        from curl_cffi import requests

        response = requests.get(url, timeout=120)
        if response.status_code < 200 or response.status_code >= 300:
            raise ProfessionalVideoUploadError(f"video download failed: HTTP {response.status_code}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return item


professional_video_asset_service = ProfessionalVideoAssetService()
