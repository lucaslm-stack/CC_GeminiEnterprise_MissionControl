# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

# Domain Pass-Through identity mapper
import logging
from typing import Iterable, List
from src.core.base import BaseIdentityMapper
from src.core.models import PipelineContext

logger = logging.getLogger("connector.github.identity.domain_pass_through")

class DomainPassThroughIdentityMapper(BaseIdentityMapper):
    """Resolves collaborator logins by appending a configured corporate domain to form SSO emails."""
    
    def __init__(self, domain: str):
        self.domain = domain.strip().lstrip("@")
        if not self.domain:
            raise ValueError("Domain pass-through mapper requires a non-empty corporate 'domain' parameter.")

    def map_identities(self, logins: Iterable[str], repository: str, context: PipelineContext) -> List[str]:
        logger.info(f"[{repository}] Mapping collaborators via domain pass-through (@{self.domain})...")
        return [f"{login}@{self.domain}" if login else None for login in logins]
