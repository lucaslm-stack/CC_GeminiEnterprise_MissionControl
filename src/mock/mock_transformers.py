# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
from typing import Optional
from google.cloud import discoveryengine_v1 as discoveryengine
from ..core.base import BaseDocumentTransformer
from ..core.models import RawPayload, PipelineContext

logger = logging.getLogger("connector.mock.transformers")

class RESTDocumentTransformer(BaseDocumentTransformer):
    """Maps legacy unstructured REST payloads into standardized search-ready documents with Pure ACLs."""
    
    def transform(self, data: RawPayload, context: PipelineContext) -> Optional[discoveryengine.Document]:
        if not isinstance(data.data, dict):
            raise TypeError(f"RESTDocumentTransformer expects a dictionary payload; received {type(data.data).__name__}")
            
        payload = data.data
        doc_id = payload.get("id")
        
        if not doc_id:
            logger.warning("Received raw record with missing 'id' property; skipping.")
            return None
            
        import base64
        
        # Extract content text and structural metadata fields
        content_text = payload.get("content")
        content_base64 = payload.get("content_base64")
        title = payload.get("title", "Untitled Document")
        department = payload.get("department_code", "General")
        doc_type = payload.get("mime", "text/html")
        
        struct_data = {
            "title": title,
            "department": department,
            "mimeType": doc_type,
            "version": payload.get("version", "1.0_rest"),
            "sys_audit_node": payload.get("database_cluster_node", "primary")
        }
        
        # ==========================================
        # PURE ACL ENFORCEMENT
        # ==========================================
        # The source data already contains direct Workspace Google emails under "permissions"
        legacy_permissions = payload.get("permissions", [])
        acl_info = None
        readers_list = []
        
        if legacy_permissions:
            for perm in legacy_permissions:
                # Direct workspace identities: user email or Google group email
                email = perm.get("email")
                identity_type = perm.get("type", "USER").upper() # "USER" or "GROUP"
                
                if email:
                    if identity_type == "GROUP":
                        readers_list.append({"group_id": email})
                    else:
                        readers_list.append({"user_id": email})
            
            if readers_list:
                acl_info = {
                    "readers": readers_list
                }
                
        # Assemble the clean target contract
        doc_content = None
        if content_base64 is not None:
            doc_content = discoveryengine.Document.Content(
                mime_type=doc_type,
                raw_bytes=base64.b64decode(content_base64)
            )
        elif content_text is not None:
            doc_content = discoveryengine.Document.Content(
                mime_type=doc_type,
                raw_bytes=content_text.encode("utf-8")
            )

        native_acl_info = None
        if acl_info and "readers" in acl_info:
            principals = []
            is_idp_wide = False
            for reader in acl_info["readers"]:
                user_id = reader.get("user_id")
                group_id = reader.get("group_id")
                if user_id in ("allAuthenticatedUsers", "allUsers") or group_id in ("allAuthenticatedUsers", "allUsers"):
                    is_idp_wide = True
                
                principal = discoveryengine.Principal()
                if user_id:
                    principal.user_id = user_id
                elif group_id:
                    principal.group_id = group_id
                principals.append(principal)
            
            if is_idp_wide:
                access_restriction = discoveryengine.Document.AclInfo.AccessRestriction(
                    idp_wide=True
                )
            else:
                access_restriction = discoveryengine.Document.AclInfo.AccessRestriction(
                    principals=principals
                )
            native_acl_info = discoveryengine.Document.AclInfo(readers=[access_restriction])

        processed_doc = discoveryengine.Document(
            id=doc_id,
            struct_data=struct_data,
            content=doc_content,
            acl_info=native_acl_info
        )
        
        logger.debug(f"Successfully transformed document '{doc_id}' (Pure ACL readers count: {len(readers_list) if acl_info else 0})")
        return processed_doc
