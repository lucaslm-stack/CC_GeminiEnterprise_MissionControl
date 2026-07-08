# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import logging
import os
from typing import Optional, List, Generator
from ...core.models import RawPayload, PipelineContext
from .base import _resolve_target_repositories_and_branches, _resolve_default_branch_name, BaseGitHubFetcher

logger = logging.getLogger("connector.github.fetchers.file")

class GitHubRepositoryFileFetcher(BaseGitHubFetcher):
    """Fetches repository source files recursively from GitHub Enterprise v3.18, with Git OID caching."""
    
    def __init__(
        self,
        app_id: str,
        installation_id: str,
        repo: Optional[str] = None,
        repos: Optional[List[str]] = None,
        private_key_path: Optional[str] = None,
        private_key_secret_name: Optional[str] = None,
        base_url: str = "https://api.github.com",
        redis_host: Optional[str] = None,
        exclude_extensions: Optional[List[str]] = None,
        exclude_paths: Optional[List[str]] = None,
        include_extensions: Optional[List[str]] = None,
        include_filenames: Optional[List[str]] = None,
        include_paths: Optional[List[str]] = None
    ):
        self.owner = None
        super().__init__(
            app_id=app_id,
            installation_id=installation_id,
            repo=repo,
            repos=repos,
            private_key_path=private_key_path,
            private_key_secret_name=private_key_secret_name,
            base_url=base_url,
            redis_host=redis_host
        )
        
        # Configure exclusions with sensible defaults
        self.exclude_extensions = exclude_extensions if exclude_extensions is not None else [
            ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", 
            ".lock", ".log", ".exe", ".bin"
        ]
        self.exclude_paths = exclude_paths if exclude_paths is not None else [
            "node_modules/", ".venv/", "venv/", ".git/", "dist/", "build/", "bin/"
        ]
        
        # Configure inclusions for documentation-only indexing
        self.include_extensions = include_extensions if include_extensions is not None else [
            ".md", ".markdown", ".txt", ".html", ".htm"
        ]
        self.include_filenames = include_filenames if include_filenames is not None else [
            "readme"
        ]
        self.include_paths = include_paths

    def _should_exclude(self, path: str) -> bool:
        """Checks if a file path matches extensions or path prefix blacklists."""
        # Check extensions
        for ext in self.exclude_extensions:
            if path.lower().endswith(ext.lower()):
                return True
        # Check path prefixes/subdirectories
        for pfx in self.exclude_paths:
            pfx_clean = pfx.rstrip("/")
            if path == pfx_clean or path.startswith(pfx_clean + "/"):
                return True
            # Also catch if it is deep in a path (e.g. some_dir/node_modules/file.txt or some_dir/node_modules)
            if f"/{pfx_clean}/" in path or path.endswith(f"/{pfx_clean}"):
                return True
        return False

    def _should_traverse_dir(self, dir_path: str) -> bool:
        """Determines if a directory should be entered based on include/exclude paths."""
        if self._should_exclude(dir_path):
            return False
            
        if not self.include_paths:
            return True
            
        dir_path_clean = dir_path.lower().rstrip("/")
        for inc in self.include_paths:
            inc_clean = inc.lower().rstrip("/")
            # Case A: We are inside or equal to a whitelisted directory
            if dir_path_clean == inc_clean or dir_path_clean.startswith(inc_clean + "/"):
                return True
            # Case B: The directory is a parent of a whitelisted path
            if inc_clean.startswith(dir_path_clean + "/"):
                return True
                
        return False

    def _should_include(self, path: str) -> bool:
        """Checks if a file path matches extensions or filename whitelists."""
        filename = os.path.basename(path).lower()
        
        # 1. If include_paths is configured, verify the file path is under one of the paths
        if self.include_paths:
            path_clean = path.lower()
            under_include_path = False
            for inc in self.include_paths:
                inc_clean = inc.lower().rstrip("/")
                if path_clean == inc_clean or path_clean.startswith(inc_clean + "/"):
                    under_include_path = True
                    break
            if not under_include_path:
                return False
        
        # 2. Always include README files (case-insensitive and prefix match)
        for name in self.include_filenames:
            if filename == name or filename.startswith(name + "."):
                return True
                
        # 3. Check whitelisted extensions
        for ext in self.include_extensions:
            if path.lower().endswith(ext.lower()):
                return True
                
        return False

    def fetch(self, context: PipelineContext) -> Generator[RawPayload, None, None]:
        target_repos = _resolve_target_repositories_and_branches(self.client, self.repo, self.repos, "HEAD")
        logger.info(f"Resolved {len(target_repos)} repositories to sync: {list(target_repos.keys())}")
        
        recon_mode = context.config.get("reconciliation_mode", "INCREMENTAL").upper()
        
        # 1. Query the repository file structure recursively using GraphQL
        # expression "HEAD:" returns the root tree
        query = """
        query GetRepoTree($owner: String!, $repo: String!, $expression: String!) {
          repository(owner: $owner, name: $repo) {
            object(expression: $expression) {
              ... on Tree {
                entries {
                  name
                  path
                  type
                  oid
                }
              }
            }
          }
        }
        """
        
        file_query = """
        query GetFileContent($owner: String!, $repo: String!, $expression: String!) {
          repository(owner: $owner, name: $repo) {
            object(expression: $expression) {
              ... on Blob {
                text
                isBinary
                byteSize
              }
            }
          }
        }
        """
        
        for repo_full_name, branch in target_repos.items():
            owner, repo_name = repo_full_name.split("/", 1)
            self.owner = owner
            
            if branch == "HEAD":
                resolved = _resolve_default_branch_name(self.client, owner, repo_name)
                if not resolved:
                    logger.warning(f"[{owner}/{repo_name}] Repository is empty or default branch could not be resolved. Skipping.")
                    continue
                branch = resolved
                
            logger.info(f"[{self.owner}/{repo_name}] Starting repository file extraction on branch: {branch}")
            
            # BFS stack to traverse directories recursively
            queue = [branch + ":"]
            
            while queue:
                expression = queue.pop(0)
                variables = {
                    "owner": self.owner,
                    "repo": repo_name,
                    "expression": expression
                }
                
                try:
                    data = self.client.execute_graphql(query, variables)
                except Exception as e:
                    logger.error(f"[{self.owner}/{repo_name}] Failed to query repository tree for expression '{expression}': {e}")
                    context.record_error("github_file_fetcher", f"{repo_name}:{expression}", e)
                    continue
 
                repository_node = data.get("repository")
                if not repository_node:
                    logger.warning(f"Repository {self.owner}/{repo_name} not found.")
                    continue
 
                object_node = repository_node.get("object")
                if not object_node:
                    continue
 
                entries = object_node.get("entries", [])
                for entry in entries:
                    path = entry["path"]
                    entry_type = entry["type"]
                    oid = entry["oid"] # Unique Git Object ID (SHA) of the file version
                    
                    if self._should_exclude(path):
                        logger.debug(f"Skipping excluded path: {path}")
                        continue
 
                    if entry_type == "tree":
                        if self._should_traverse_dir(path):
                            # Append directory tree to queue for recursive traversal
                            queue.append(f"{branch}:{path}")
                        continue
                        
                    if entry_type == "blob":
                        # Only ingest whitelisted documentation files
                        if not self._should_include(path):
                            logger.debug(f"Skipping non-documentation file path: {path}")
                            continue
                            
                        # ==========================================
                        # RIGOROUS GIT OID CACHING
                        # ==========================================
                        # Key by repository + path + git OID (immutable)
                        cache_key = f"github_file:{self.owner}/{repo_name}:{path}:{oid}"
                        cached_file_data = self.cache.get(cache_key) if recon_mode == "INCREMENTAL" else None
                        
                        if cached_file_data:
                            logger.info(f"[{self.owner}/{repo_name}] Cache Hit for immutable file: {path} (OID: {oid}). Bypassing API.")
                            yield RawPayload(data=cached_file_data)
                            continue
                            
                        # Cache Miss: Query file contents from GitHub
                        logger.info(f"[{self.owner}/{repo_name}] Cache Miss for file: {path} (OID: {oid}). Querying GitHub API...")
                        file_vars = {
                            "owner": self.owner,
                            "repo": repo_name,
                            "expression": f"{branch}:{path}"
                        }
                        
                        try:
                            file_data = self.client.execute_graphql(file_query, file_vars)
                            blob_node = file_data.get("repository", {}).get("object", {})
                            
                            if blob_node and not blob_node.get("isBinary", False):
                                file_payload = {
                                    "id": f"{self.owner}/{repo_name}:{path}",
                                    "path": path,
                                    "oid": oid,
                                    "content": blob_node.get("text", ""),
                                    "byte_size": blob_node.get("byteSize", 0),
                                    "repository": f"{self.owner}/{repo_name}",
                                    "branch": branch
                                }
                                
                                # Cache indefinitely (immutable for this OID)
                                self.cache.set(cache_key, file_payload, expire_seconds=86400 * 30) # Cache for 30 days
                                yield RawPayload(data=file_payload)
                            else:
                                logger.info(f"[{self.owner}/{repo_name}] Skipping binary file blob: {path}")
                        except Exception as e:
                            logger.warning(f"[{self.owner}/{repo_name}] Failed to fetch content for file {path}: {e}")
                            context.record_error("github_file_fetcher", f"{repo_name}:{path}", e)
                            continue
