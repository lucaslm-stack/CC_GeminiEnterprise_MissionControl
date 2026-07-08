# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

# Basic mapping identity mapper
import os
import yaml
import logging
from typing import Iterable, List
from src.core.base import BaseIdentityMapper
from src.core.models import PipelineContext

logger = logging.getLogger("connector.github.identity.basic_mapping")

class UnifiedRepositoryIdentityMapper(BaseIdentityMapper):
    """Unified identity mapper supporting groups, specific emails, public access, and default wildcards."""
    
    def __init__(self, config_file: str, default_public_email: str = "allUsers"):
        self.config_file = config_file
        self.default_public_email = default_public_email
        self.groups = {}
        self.mappings = {}
        self._load_config()

    def _load_config(self):
        if not os.path.exists(self.config_file):
            logger.error(f"Unified config file not found: {self.config_file}")
            return
        try:
            with open(self.config_file, "r") as f:
                config = yaml.safe_load(f) or {}
                
                # 1. Parse Groups
                raw_groups = config.get("groups") or {}
                if isinstance(raw_groups, list):
                    for item in raw_groups:
                        if isinstance(item, dict):
                            for gname, gemails in item.items():
                                self.groups[gname] = gemails if isinstance(gemails, list) else [gemails]
                elif isinstance(raw_groups, dict):
                    for gname, gemails in raw_groups.items():
                        self.groups[gname] = gemails if isinstance(gemails, list) else [gemails]

                # 2. Parse Mappings
                for entry in config.get("mappings", []):
                    repo = entry.get("repository")
                    if not repo:
                        continue
                    
                    repo_key = repo.lower()
                    if entry.get("isPublic", False):
                        self.mappings[repo_key] = [self.default_public_email]
                    else:
                        emails = []
                        permissions = entry.get("permissions", {}) or {}
                        
                        groups_to_add = []
                        emails_to_add = []
                        
                        if isinstance(permissions, list):
                            for item in permissions:
                                if isinstance(item, dict):
                                    if "groups" in item:
                                        groups_to_add.extend(item["groups"] or [])
                                    if "emails" in item:
                                        emails_to_add.extend(item["emails"] or [])
                        elif isinstance(permissions, dict):
                            groups_to_add.extend(permissions.get("groups") or [])
                            emails_to_add.extend(permissions.get("emails") or [])
                        
                        # Expand groups
                        for gname in groups_to_add:
                            emails.extend(self.groups.get(gname, []))
                            
                        # Add explicit emails
                        emails.extend(emails_to_add)
                        
                        self.mappings[repo_key] = list(dict.fromkeys(emails))
                        
            logger.info(f"Loaded {len(self.mappings)} unified mappings and {len(self.groups)} groups from {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to load unified config file {self.config_file}: {e}")

    def map_identities(self, logins: Iterable[str], repository: str, context: PipelineContext) -> List[str]:
        repo_lower = repository.lower()
        if repo_lower in self.mappings:
            return self.mappings[repo_lower]
        # Wildcard fallback if defined
        if "*" in self.mappings:
            return self.mappings["*"]
        return []

# Alias for backwards compatibility or basicMapping style name
BasicRepositoryIdentityMapper = UnifiedRepositoryIdentityMapper
