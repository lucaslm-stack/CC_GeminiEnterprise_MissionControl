# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import re
from typing import List, Optional
from google.cloud import discoveryengine_v1 as discoveryengine

def _sanitize_doc_id(doc_id: str) -> str:
    """Sanitizes document IDs to strictly conform to Discovery Engine constraints: ^[a-zA-Z0-9-_]+$"""
    return re.sub(r'[^a-zA-Z0-9-_]', '_', doc_id)

def _get_web_base_url(api_url: str) -> str:
    """Converts a GitHub API base URL to a GitHub Web UI base URL."""
    if "api.github.com" in api_url:
        return "https://github.com"
    url = api_url
    if url.endswith("/api/v3"):
        url = url[:-7]
    elif url.endswith("/api/v3/"):
        url = url[:-8]
    return url

def _extract_jira_tickets(text: Optional[str]) -> List[str]:
    """Extracts unique Jira ticket IDs matching standard format like PROJ-123 from text blocks."""
    if not text:
        return []
    pattern = r'\b([A-Z][A-Z0-9]{1,9}-\d+)\b'
    matches = re.findall(pattern, text)
    return sorted(list(set(matches)))

def _build_native_acl_info(principals: List[discoveryengine.Principal]) -> Optional[discoveryengine.Document.AclInfo]:
    if not principals:
        return None
        
    is_idp_wide = any(p.user_id in ("allAuthenticatedUsers", "allUsers") for p in principals)
    if is_idp_wide:
        access_restriction = discoveryengine.Document.AclInfo.AccessRestriction(
            idp_wide=True
        )
    else:
        access_restriction = discoveryengine.Document.AclInfo.AccessRestriction(
            principals=principals
        )
    return discoveryengine.Document.AclInfo(readers=[access_restriction])
