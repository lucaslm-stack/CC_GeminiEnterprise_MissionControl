# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import unittest
from unittest.mock import MagicMock, patch
from src.core.models import PipelineContext
from src.github.fetchers import GitHubRepositoryFileFetcher
from src.github.fetchers.base import _resolve_target_repositories_and_branches, _resolve_default_branch_name

class FailureException(BaseException):
    pass

class TestGitHubRepositoryFileFetcher(unittest.TestCase):
    
    @patch("src.github.fetchers.base.GitHubAppClient")
    @patch("src.github.fetchers.base.PipelineCache")
    def test_fetcher_traversal_and_filtering(self, mock_cache_class, mock_client_class):
        # Setup mocks
        mock_client = mock_client_class.return_value
        mock_cache = mock_cache_class.return_value
        mock_cache.get.return_value = None # Cache miss to trigger API call
        
        # Mock repository list resolution
        mock_client.get_installation_repositories.return_value = ["owner/repo-a"]
        
        # Instantiate fetcher
        fetcher = GitHubRepositoryFileFetcher(
            app_id="123",
            installation_id="456",
            repos=["^owner/repo-a$:main"],
            exclude_paths=["node_modules/", ".git/"],
            include_extensions=[".md", ".txt"],
            include_filenames=["readme"]
        )
        
        # Re-inject the mocked client
        fetcher.client = mock_client
        fetcher.cache = mock_cache
        
        # Mock tree responses
        # Expression "main:" (root tree)
        root_tree = {
            "repository": {
                "object": {
                    "entries": [
                        {"name": "README.md", "path": "README.md", "type": "blob", "oid": "oid-readme"},
                        {"name": "main.py", "path": "main.py", "type": "blob", "oid": "oid-mainpy"},
                        {"name": "docs", "path": "docs", "type": "tree", "oid": "oid-docs-dir"},
                        {"name": "node_modules", "path": "node_modules", "type": "tree", "oid": "oid-nodemodules-dir"}
                    ]
                }
            }
        }
        
        # Expression "main:docs" (docs subdir tree)
        docs_tree = {
            "repository": {
                "object": {
                    "entries": [
                        {"name": "guide.txt", "path": "docs/guide.txt", "type": "blob", "oid": "oid-guide"},
                        {"name": "unsupported.png", "path": "docs/unsupported.png", "type": "blob", "oid": "oid-png"}
                    ]
                }
            }
        }
        
        # File contents responses
        readme_content = {
            "repository": {
                "object": {
                    "text": "This is a readme file.",
                    "isBinary": False,
                    "byteSize": 22
                }
            }
        }
        
        guide_content = {
            "repository": {
                "object": {
                    "text": "This is a guide.",
                    "isBinary": False,
                    "byteSize": 16
                }
            }
        }
        
        # GraphQL router mock function
        def mock_execute_graphql(query, variables):
            expr = variables.get("expression", "")
            if "GetRepoTree" in query or "object(expression:" in query and "entries" in query:
                if expr == "main:":
                    return root_tree
                elif expr == "main:docs":
                    return docs_tree
                elif expr == "main:node_modules":
                    raise FailureException("Fetcher traversed into excluded node_modules/ directory!")
            elif "GetFileContent" in query or "Blob" in query:
                if expr == "main:README.md":
                    return readme_content
                elif expr == "main:docs/guide.txt":
                    return guide_content
                elif expr == "main:main.py":
                    raise FailureException("Fetcher attempted to download content for non-whitelisted main.py!")
                elif expr == "main:docs/unsupported.png":
                    raise FailureException("Fetcher attempted to download content for non-whitelisted unsupported.png!")
            return {}
            
        mock_client.execute_graphql.side_effect = mock_execute_graphql
        
        # Run fetch
        context = PipelineContext(config={"reconciliation_mode": "FULL"})
        payloads = list(fetcher.fetch(context))
        
        # We expect exactly 2 files: README.md and docs/guide.txt
        self.assertEqual(len(payloads), 2)
        
        # Verify first payload
        readme_payload = next(p.data for p in payloads if p.data["path"] == "README.md")
        self.assertEqual(readme_payload["content"], "This is a readme file.")
        self.assertEqual(readme_payload["oid"], "oid-readme")
        self.assertEqual(readme_payload["repository"], "owner/repo-a")
        
        # Verify second payload
        guide_payload = next(p.data for p in payloads if p.data["path"] == "docs/guide.txt")
        self.assertEqual(guide_payload["content"], "This is a guide.")
        self.assertEqual(guide_payload["oid"], "oid-guide")

    @patch("src.github.fetchers.base.GitHubAppClient")
    @patch("src.github.fetchers.base.PipelineCache")
    def test_fetcher_directory_scoping_inclusion(self, mock_cache_class, mock_client_class):
        # Setup mocks
        mock_client = mock_client_class.return_value
        mock_cache = mock_cache_class.return_value
        mock_cache.get.return_value = None
        
        mock_client.get_installation_repositories.return_value = ["owner/repo-a"]
        
        # Instantiate fetcher with include_paths
        fetcher = GitHubRepositoryFileFetcher(
            app_id="123",
            installation_id="456",
            repos=["^owner/repo-a$:main"],
            include_paths=["docs/", "README.md"]
        )
        
        fetcher.client = mock_client
        fetcher.cache = mock_cache
        
        # Tree structure with non-whitelisted "src" directory
        root_tree = {
            "repository": {
                "object": {
                    "entries": [
                        {"name": "README.md", "path": "README.md", "type": "blob", "oid": "oid-readme"},
                        {"name": "docs", "path": "docs", "type": "tree", "oid": "oid-docs-dir"},
                        {"name": "src", "path": "src", "type": "tree", "oid": "oid-src-dir"}
                    ]
                }
            }
        }
        
        docs_tree = {
            "repository": {
                "object": {
                    "entries": [
                        {"name": "guide.txt", "path": "docs/guide.txt", "type": "blob", "oid": "oid-guide"}
                    ]
                }
            }
        }
        
        readme_content = {
            "repository": {
                "object": {
                    "text": "Readme content",
                    "isBinary": False,
                    "byteSize": 14
                }
            }
        }
        
        guide_content = {
            "repository": {
                "object": {
                    "text": "Guide content",
                    "isBinary": False,
                    "byteSize": 13
                }
            }
        }
        
        def mock_execute_graphql(query, variables):
            expr = variables.get("expression", "")
            if "GetRepoTree" in query or "object(expression:" in query and "entries" in query:
                if expr == "main:":
                    return root_tree
                elif expr == "main:docs":
                    return docs_tree
                elif expr == "main:src":
                    raise FailureException("Fetcher traversed into non-whitelisted src/ directory!")
            elif "GetFileContent" in query or "Blob" in query:
                if expr == "main:README.md":
                    return readme_content
                elif expr == "main:docs/guide.txt":
                    return guide_content
            return {}
            
        mock_client.execute_graphql.side_effect = mock_execute_graphql
        
        context = PipelineContext(config={"reconciliation_mode": "FULL"})
        payloads = list(fetcher.fetch(context))
        
        self.assertEqual(len(payloads), 2)
        paths = {p.data["path"] for p in payloads}
        self.assertEqual(paths, {"README.md", "docs/guide.txt"})

    def test_resolve_target_repositories_and_branches(self):
        mock_client = MagicMock()
        mock_client.get_installation_repositories.return_value = [
            "MyReddit/gangplank-demo",
            "MyReddit/gep-custom-connectors",
            "MyReddit/test_bot",
            "OtherOrg/other-repo"
        ]
        
        # Test 1: Single repo override
        res = _resolve_target_repositories_and_branches(mock_client, repo="MyReddit/test_bot", repos=None, default_branch="main")
        self.assertEqual(res, {"MyReddit/test_bot": "main"})
        
        # Test 2: Single repo with explicit branch override
        res = _resolve_target_repositories_and_branches(mock_client, repo="MyReddit/test_bot:master", repos=None, default_branch="main")
        self.assertEqual(res, {"MyReddit/test_bot": "master"})
        
        # Test 3: Multiple patterns (including repo-specific branch overrides and generic fallback branch)
        patterns = [
            "^MyReddit/gangplank-demo:master",
            "^MyReddit/gep-custom-connectors:develop",
            "^MyReddit/.*" # defaults to "main"
        ]
        res = _resolve_target_repositories_and_branches(mock_client, repo=None, repos=patterns, default_branch="main")
        self.assertEqual(res, {
            "MyReddit/gangplank-demo": "master",
            "MyReddit/gep-custom-connectors": "develop",
            "MyReddit/test_bot": "main"
        })

    def test_resolve_default_branch_name(self):
        mock_client = MagicMock()
        
        # Test case 1: Successful resolution
        mock_client.execute_graphql.return_value = {
            "repository": {
                "defaultBranchRef": {
                    "name": "master"
                }
            }
        }
        branch = _resolve_default_branch_name(mock_client, "owner", "repo")
        self.assertEqual(branch, "master")
        mock_client.execute_graphql.assert_called_with(unittest.mock.ANY, {"owner": "owner", "repo": "repo"})
        
        # Test case 2: Empty defaultBranchRef (empty repository)
        mock_client.execute_graphql.return_value = {
            "repository": {
                "defaultBranchRef": None
            }
        }
        branch_empty = _resolve_default_branch_name(mock_client, "owner", "repo")
        self.assertIsNone(branch_empty)
        
        # Test case 3: API error / exception thrown
        mock_client.execute_graphql.side_effect = Exception("API rate limit")
        branch_error = _resolve_default_branch_name(mock_client, "owner", "repo")
        self.assertIsNone(branch_error)

if __name__ == "__main__":
    unittest.main()
