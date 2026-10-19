"""The relay's refusal codes and their statuses (ADR 0058, ADR 0060).

Every code has one status. The relay's own codes are in RELAY_CODES; Door's
codes (ADR 0058) pass through unchanged with the statuses in DOOR_STATUSES.
relay/openapi.json lists every one, and a test keeps the two equal.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

RELAY_CODES: dict[str, HTTPStatus] = {
    "request_malformed": HTTPStatus.BAD_REQUEST,
    "forbidden_identity": HTTPStatus.FORBIDDEN,
    "forbidden_origin": HTTPStatus.FORBIDDEN,
    "agent_mismatch": HTTPStatus.FORBIDDEN,
    "commits_disabled": HTTPStatus.FORBIDDEN,
    "not_found": HTTPStatus.NOT_FOUND,
    "method_not_allowed": HTTPStatus.METHOD_NOT_ALLOWED,
    "length_required": HTTPStatus.LENGTH_REQUIRED,
    "body_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "unsupported_media_type": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    "content_not_relayed": HTTPStatus.UNPROCESSABLE_ENTITY,
    "query_invalid": HTTPStatus.BAD_REQUEST,
    "query_refused": HTTPStatus.BAD_REQUEST,
    "query_too_many_rows": HTTPStatus.UNPROCESSABLE_ENTITY,
    "projection_not_served": HTTPStatus.NOT_FOUND,
    "query_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "world_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "door_unreachable": HTTPStatus.SERVICE_UNAVAILABLE,
    "door_failed": HTTPStatus.BAD_GATEWAY,
    "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
    "media_forbidden": HTTPStatus.FORBIDDEN,
    "media_unknown_digest": HTTPStatus.NOT_FOUND,
    "media_not_derived": HTTPStatus.NOT_FOUND,
    "range_not_satisfiable": HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
    # Named views (ADR 0082).
    "view_unknown": HTTPStatus.NOT_FOUND,
    "view_not_served": HTTPStatus.NOT_FOUND,
    "view_request_invalid": HTTPStatus.BAD_REQUEST,
    "view_subject_not_found": HTTPStatus.NOT_FOUND,
    # Bandmates (ADR 0088).
    "operator_only": HTTPStatus.FORBIDDEN,
    "not_disclosed": HTTPStatus.FORBIDDEN,
    # Documents (ADR 0085).
    "content_audio_or_video": HTTPStatus.UNPROCESSABLE_ENTITY,
    "media_type_unsupported": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    "content_mismatch": HTTPStatus.UNPROCESSABLE_ENTITY,
    "content_not_described": HTTPStatus.UNPROCESSABLE_ENTITY,
    # Practice audio and exports (ADR 0089).
    "feature_not_served": HTTPStatus.NOT_FOUND,
    "feature_not_made": HTTPStatus.NOT_FOUND,
    "stems_not_made": HTTPStatus.NOT_FOUND,
    "renderer_unknown": HTTPStatus.UNPROCESSABLE_ENTITY,
    "render_failed": HTTPStatus.BAD_GATEWAY,
    "exporter_unknown": HTTPStatus.UNPROCESSABLE_ENTITY,
    "export_failed": HTTPStatus.BAD_GATEWAY,
    "export_input_unknown": HTTPStatus.NOT_FOUND,
}
DOOR_STATUSES: dict[str, HTTPStatus] = {
    "request_malformed": HTTPStatus.BAD_GATEWAY,
    "proposal_malformed": HTTPStatus.UNPROCESSABLE_ENTITY,
    "proposal_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "expected_head_stale": HTTPStatus.CONFLICT,
    "archive_unreadable": HTTPStatus.SERVICE_UNAVAILABLE,
    "integrity_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "configuration_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "io_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "subprocess_failure": HTTPStatus.INTERNAL_SERVER_ERROR,
    "operation_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
    "outcome_uncertain": HTTPStatus.SERVICE_UNAVAILABLE,
    "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class Refused(Exception):
    """A request the relay will not answer with success: a code, its HTTP
    status, and a message. ``extra`` carries Door's other refusal fields,
    and ``headers`` any header the refusal must carry."""

    def __init__(self, code: str, message: str, status: HTTPStatus | None = None,
                 extra: dict[str, Any] | None = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status or RELAY_CODES[code]
        self.extra = extra or {}
        self.headers = headers or {}

    def error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.extra}


def coded(error: Exception) -> Refused:
    """A rule module's error (it carries `code`) as the relay's refusal."""
    code = getattr(error, "code", "request_malformed")
    return Refused(code if code in RELAY_CODES else "request_malformed", str(error))
