# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import json
import logging
import re
import datetime
from typing import Optional, Dict, Any, List
from google.cloud import discoveryengine_v1 as discoveryengine
from ..core.base import BaseDocumentTransformer
from ..core.models import RawPayload, PipelineContext

logger = logging.getLogger("connector.bigquery.transformers")

INTERNAL_AUTHOR_FALLBACK = "mission.control.internal.user@reddit.com"


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return super().default(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, cls=DateTimeEncoder)


def _convert_datetimes(obj: Any) -> Any:
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _convert_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_datetimes(i) for i in obj]
    return obj


def _sanitize_doc_id(doc_id: str) -> str:
    """Sanitizes document IDs to strictly conform to Discovery Engine constraints: ^[a-zA-Z0-9-_]+$"""
    return re.sub(r'[^a-zA-Z0-9-_]', '_', str(doc_id))


def _int_or_none(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _prefix_id(prefix: str, raw: Any) -> str:
    """Attach a prefix idempotently — 'commitment-501' stays 'commitment-501'."""
    s = str(raw)
    if s.startswith(f"{prefix}-"):
        return s
    return f"{prefix}-{s}"


class SnoogutsTransformer(BaseDocumentTransformer):
    """
    Transforms Snooguts Mission Control raw payloads into Discovery Engine Documents.

    Emits documents in the shape the Gemini Enterprise Custom Connector expects:
    a single `json_data` string carrying the full Snooguts payload (per
    sync_schema.txt §5), plus a top-level `acl_info` for ACL enforcement.

    Does NOT set `content` — this is a metadata-only custom connector and its
    target data store must be `contentConfig: NO_CONTENT`. Setting `content` with
    an empty `raw_bytes` (as the previous implementation did) causes Discovery
    Engine to reject every document with `INCORRECT_JSON_FORMAT`.
    """

    def __init__(self, datasource: str = "snoogutsmissioncontrol"):
        self.datasource = datasource

    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        payload = _convert_datetimes(data.data)
        if not isinstance(payload, dict):
            return None

        entity_type = payload.get("entityType")
        if not entity_type:
            return None

        action = payload.get("action", "upsert")
        if action == "delete":
            # Deletes are handled via a separate sync path (see sync_schema.txt §3).
            return None

        if entity_type == "initiative":
            return self._transform_initiative(payload)
        if entity_type == "commitment":
            return self._transform_commitment(payload)
        if entity_type == "launch":
            return self._transform_launch(payload)
        if entity_type == "person":
            return self._transform_person(payload)
        return None

    def _build_permissions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Builds the `permissions` block per sync_schema.txt §5."""
        is_private = bool(payload.get("private", False))
        allowed_users = payload.get("allowedUsers") or []
        perms: Dict[str, Any] = {"allowAnonymousAccess": not is_private}
        if is_private and allowed_users:
            perms["allowedUsers"] = [{"email": e} for e in allowed_users]
        return perms

    def _build_acl_info(self, payload: Dict[str, Any]) -> Optional[discoveryengine.Document.AclInfo]:
        is_private = bool(payload.get("private", False))
        allowed_users = payload.get("allowedUsers") or []
        owner = payload.get("ownerEmail")

        if not is_private:
            return discoveryengine.Document.AclInfo(
                readers=[discoveryengine.Document.AclInfo.AccessRestriction(idp_wide=True)]
            )

        principals = [discoveryengine.Principal(user_id=e) for e in allowed_users]
        if not principals and owner:
            principals.append(discoveryengine.Principal(user_id=owner))
        if not principals:
            return None
        return discoveryengine.Document.AclInfo(
            readers=[discoveryengine.Document.AclInfo.AccessRestriction(principals=principals)]
        )

    def _base_payload(
        self,
        payload: Dict[str, Any],
        object_type: str,
        doc_id: str,
        view_url: str,
        author_email: str,
    ) -> Dict[str, Any]:
        """Shared Snooguts document envelope per sync_schema.txt §5."""
        return {
            "datasource": self.datasource,
            "objectType": object_type,
            "id": doc_id,
            "title": payload.get("title", ""),
            "body": {
                "mimeType": "text/plain",
                "textContent": payload.get("description") or "",
            },
            "viewURL": view_url,
            "createdAt": _int_or_none(payload.get("createdAt")),
            "updatedAt": _int_or_none(payload.get("updatedAt")),
            "permissions": self._build_permissions(payload),
            "author": {"email": author_email},
            "customProperties": [],
        }

    @staticmethod
    def _add_prop(props: List[Dict[str, Any]], name: str, value: Any) -> None:
        """Only include truthy properties, per sync_schema.txt §5.

        Coerces `value` to a string. Discovery Engine's inferred schema for
        `customProperties.value` locks to a single scalar type on first ingest;
        keeping every value as a string sidesteps schema-type conflicts across
        entities (mixing strings + ints + arrays throws
        "Unexpected value of array type / Unable to parse ..." errors).
        Arrays/objects are JSON-encoded — matches sync_schema.txt's
        `hierarchyTeam: string (JSON-encoded array)` pattern.
        """
        if value is None:
            return
        if isinstance(value, (list, str)) and len(value) == 0:
            return
        if isinstance(value, str):
            coerced = value
        elif isinstance(value, bool):
            coerced = "True" if value else "False"
        elif isinstance(value, (int, float)):
            coerced = str(value)
        else:
            coerced = _json_dumps(value)
        if coerced == "":
            return
        props.append({"name": name, "value": coerced})

    def _finalize(
        self,
        doc_id: str,
        snooguts_payload: Dict[str, Any],
        payload: Dict[str, Any],
        acl_info: Optional[discoveryengine.Document.AclInfo],
    ) -> discoveryengine.Document:
        interactions = payload.get("interactions")
        if isinstance(interactions, dict) and interactions:
            snooguts_payload["interactions"] = {
                "numLikes": _int_or_none(interactions.get("numLikes")) or 0,
                "numComments": _int_or_none(interactions.get("numComments")) or 0,
                "numViews": _int_or_none(interactions.get("numViews")) or 0,
            }

        return discoveryengine.Document(
            id=_sanitize_doc_id(doc_id),
            json_data=_json_dumps(snooguts_payload),
            acl_info=acl_info,
        )

    def _transform_initiative(self, payload: Dict[str, Any]) -> discoveryengine.Document:
        program_id = payload.get("id")
        doc_id = f"initiative-{program_id}"
        base = self._base_payload(
            payload,
            object_type="Initiatives",
            doc_id=doc_id,
            view_url=payload.get("url") or f"https://launch.snooguts.net/programs/{program_id}",
            author_email=payload.get("ownerEmail") or INTERNAL_AUTHOR_FALLBACK,
        )
        props = base["customProperties"]

        self._add_prop(props, "startDate", payload.get("startDate"))
        self._add_prop(props, "endDate", payload.get("endDate"))
        self._add_prop(props, "programStatus", payload.get("programStatus"))
        self._add_prop(props, "primaryPillar", payload.get("primaryPillar"))
        self._add_prop(props, "activeQuarters", payload.get("activeQuarters"))
        self._add_prop(props, "linkedLaunchIds", payload.get("linkedLaunchIds"))
        self._add_prop(props, "linkedCommitmentIds", payload.get("linkedCommitmentIds"))

        if payload.get("hierarchyTeam"):
            self._add_prop(props, "hierarchyTeam", _json_dumps(payload["hierarchyTeam"]))
        if payload.get("statusLogs"):
            self._add_prop(props, "statusLogsJSONString", _json_dumps(payload["statusLogs"]))
        if payload.get("links"):
            self._add_prop(props, "linksJSONString", _json_dumps(payload["links"]))
        if payload.get("decisions"):
            self._add_prop(props, "decisionsJSONString", _json_dumps(payload["decisions"]))
        if payload.get("risks"):
            self._add_prop(props, "risksJsonString", _json_dumps(payload["risks"]))
        if payload.get("resources"):
            self._add_prop(props, "resourcesJsonString", _json_dumps(payload["resources"]))

        return self._finalize(doc_id, base, payload, self._build_acl_info(payload))

    def _transform_commitment(self, payload: Dict[str, Any]) -> discoveryengine.Document:
        sc_id = payload.get("id")
        doc_id = f"commitment-{sc_id}"
        base = self._base_payload(
            payload,
            object_type="Commitments",
            doc_id=doc_id,
            view_url=payload.get("url") or f"https://launch.snooguts.net/commitment/{sc_id}",
            author_email=payload.get("ownerEmail") or INTERNAL_AUTHOR_FALLBACK,
        )
        props = base["customProperties"]

        self._add_prop(props, "responsibleUser", payload.get("responsibleUser"))
        self._add_prop(props, "accountableUser", payload.get("accountableUser"))
        self._add_prop(props, "quarter", payload.get("quarter"))
        self._add_prop(props, "pillar", payload.get("pillar"))
        self._add_prop(props, "commitmentStatus", payload.get("commitmentStatus"))
        if payload.get("isClosedOut") is not None:
            # sync_schema.txt §7 wants string "True"/"False"
            self._add_prop(props, "isClosedOut", "True" if payload["isClosedOut"] else "False")

        if payload.get("statusHistory"):
            self._add_prop(props, "statusHistoryJsonString", _json_dumps(payload["statusHistory"]))

        return self._finalize(doc_id, base, payload, self._build_acl_info(payload))

    def _transform_launch(self, payload: Dict[str, Any]) -> discoveryengine.Document:
        l_id = payload.get("id")
        doc_id = f"launch-{l_id}"
        base = self._base_payload(
            payload,
            object_type="Launches",
            doc_id=doc_id,
            view_url=payload.get("url") or f"https://launch.snooguts.net/launch/{l_id}",
            author_email=payload.get("ownerEmail") or INTERNAL_AUTHOR_FALLBACK,
        )
        props = base["customProperties"]

        self._add_prop(props, "launchDate", payload.get("launchDate"))
        self._add_prop(props, "committedQuarter", payload.get("committedQuarter"))
        self._add_prop(props, "platforms", payload.get("platforms"))
        self._add_prop(props, "productSurfaces", payload.get("productSurfaces"))
        self._add_prop(props, "pillars", payload.get("pillars"))

        if payload.get("reviews"):
            self._add_prop(props, "reviewsJsonString", _json_dumps(payload["reviews"]))
        if payload.get("commitmentIds"):
            prefixed = [_prefix_id("commitment", cid) for cid in payload["commitmentIds"]]
            self._add_prop(props, "commitmentID", _json_dumps(prefixed))
        if payload.get("links"):
            self._add_prop(
                props,
                "links",
                [_json_dumps(link) for link in payload["links"]],
            )
        if payload.get("statusLogs"):
            self._add_prop(props, "statusLogsJsonString", _json_dumps(payload["statusLogs"]))

        return self._finalize(doc_id, base, payload, self._build_acl_info(payload))

    def _transform_person(self, payload: Dict[str, Any]) -> discoveryengine.Document:
        p_id = payload.get("id")
        doc_id = f"person-{p_id}"
        author_email = payload.get("email") or payload.get("ownerEmail") or INTERNAL_AUTHOR_FALLBACK
        employee_id = payload.get("employeeId") or ""
        base = self._base_payload(
            payload,
            object_type="OrgData",
            doc_id=doc_id,
            view_url=payload.get("url") or f"https://launch.snooguts.net/org?u={employee_id}",
            author_email=author_email,
        )
        # sync_schema.txt §9: OrgData is idp_wide readable (no private flag).
        base["permissions"] = {"allowAnonymousAccess": True}
        props = base["customProperties"]

        self._add_prop(props, "managerEmail", payload.get("managerEmail"))
        self._add_prop(props, "directorEmail", payload.get("directorEmail"))
        self._add_prop(props, "executiveEmail", payload.get("executiveEmail"))
        self._add_prop(props, "function", payload.get("function"))
        self._add_prop(props, "specialties", payload.get("specialties"))
        self._add_prop(props, "pillar", payload.get("pillar"))
        self._add_prop(props, "email", payload.get("email"))
        self._add_prop(props, "displayName", payload.get("displayName"))
        pos_id = _int_or_none(payload.get("positionId")) or payload.get("positionId")
        self._add_prop(props, "positionId", pos_id)
        self._add_prop(props, "positionStatus", payload.get("positionStatus"))
        self._add_prop(props, "employeeId", payload.get("employeeId"))
        self._add_prop(props, "level", payload.get("level"))
        self._add_prop(props, "hierarchyTeam", payload.get("hierarchyTeam"))
        self._add_prop(props, "assignedToTeam", payload.get("assignedToTeam"))

        acl_info = discoveryengine.Document.AclInfo(
            readers=[discoveryengine.Document.AclInfo.AccessRestriction(idp_wide=True)]
        )
        return self._finalize(doc_id, base, payload, acl_info)
