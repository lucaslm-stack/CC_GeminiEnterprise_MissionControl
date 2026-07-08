# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import unittest
from unittest.mock import MagicMock, patch
from src.core.models import PipelineContext, RawPayload
from src.core.base import BaseIdentityMapper
from src.github.transformers import GitHubFileTransformer, GitHubPullRequestTransformer, GitHubCommitTransformer, GitHubIssueTransformer
from google.cloud import discoveryengine_v1 as discoveryengine

class MockIdentityMapper(BaseIdentityMapper):
    def __init__(self, domain: str = "custom.com"):
        self.domain = domain
        
    def map_identities(self, logins, repository, context):
        return [f"{login}@{self.domain}" for login in logins]

class TestGitHubTransformers(unittest.TestCase):



    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_resolve_emails_via_commit_history_fallback(self, mock_cache_class, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_cache = mock_cache_class.return_value
        mock_cache.get.return_value = None # Cache miss
        
        transformer = GitHubFileTransformer(
            app_id="123",
            installation_id="456",
            identity_mapper={
                "class": "src.github.identity.GitHubCommitMiningIdentityMapper"
            }
        )
        
        # 1. Collaborators query: user-a has no samlIdentity or profile email
        collaborators_response = {
            "repository": {
                "collaborators": {
                    "nodes": [
                        {"login": "user-a", "email": None, "organization": None}
                    ]
                }
            }
        }
        
        # 2. Commit history query: shows commit author email for user-a
        commit_history_response = {
            "repository": {
                "defaultBranchRef": {
                    "target": {
                        "history": {
                            "nodes": [
                                {
                                    "author": {
                                        "email": "user-a@myreddit.com",
                                        "user": {"login": "user-a"}
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

        def mock_execute_graphql(query, variables):
            if "GetRepoCollaborators" in query or "GetRepoCollaboratorLogins" in query:
                return collaborators_response
            if "GetRepoCommits" in query:
                return commit_history_response
            return {}

        mock_client.execute_graphql.side_effect = mock_execute_graphql
        
        context = PipelineContext(config={"reconciliation_mode": "INCREMENTAL"})
        principals = transformer._resolve_repo_collaborator_emails("MyReddit", "test-repo", context)
        
        self.assertEqual(len(principals), 1)
        self.assertEqual(principals[0].user_id, "user-a@myreddit.com")



    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_resolve_emails_cache_hit(self, mock_cache_class, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_cache = mock_cache_class.return_value
        
        # Mock Cache Hit
        mock_cache.get.return_value = ["user-a@myreddit.com", "user-b@myreddit.com"]
        
        transformer = GitHubFileTransformer(
            app_id="123",
            installation_id="456"
        )
        
        context = PipelineContext(config={"reconciliation_mode": "INCREMENTAL"})
        principals = transformer._resolve_repo_collaborator_emails("MyReddit", "test-repo", context)
        
        # Check cached values are used
        self.assertEqual(len(principals), 2)
        emails = {p.user_id for p in principals}
        self.assertEqual(emails, {"user-a@myreddit.com", "user-b@myreddit.com"})
        
        # Assert client was NEVER invoked
        mock_client.execute_graphql.assert_not_called()

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_transformers_instantiation_and_parameters(self, mock_cache_class, mock_client_class):
        pr_transformer = GitHubPullRequestTransformer(
            app_id="123",
            installation_id="456",
            enterprise_slug="MyReddit"
        )
        self.assertEqual(pr_transformer.file_transformer.enterprise_slug, "MyReddit")

        commit_transformer = GitHubCommitTransformer(
            app_id="123",
            installation_id="456",
            enterprise_slug="MyReddit"
        )
        self.assertEqual(commit_transformer.file_transformer.enterprise_slug, "MyReddit")

    def test_extract_jira_tickets_helper(self):
        from src.github.transformers.utils import _extract_jira_tickets
        self.assertEqual(_extract_jira_tickets("Fix GEP-123"), ["GEP-123"])
        self.assertEqual(_extract_jira_tickets("CC-456 GEP-123 CC-456"), ["CC-456", "GEP-123"])
        self.assertEqual(_extract_jira_tickets("Invalid GEP-abc, 123-GEP"), [])
        self.assertEqual(_extract_jira_tickets(None), [])

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_jira_and_owner_in_file_transformer(self, mock_cache_class, mock_client_class):
        transformer = GitHubFileTransformer(app_id="123", installation_id="456")
        transformer._resolve_repo_collaborator_emails = MagicMock(return_value=[])
        transformer._resolve_repo_contributors = MagicMock(return_value=["owner@myreddit.com"])
        
        payload = RawPayload(data={
            "id": "file-1",
            "path": "docs/readme.md",
            "content": "This is a project spec for GEP-123 and GEP-456.",
            "repository": "MyReddit/test-repo",
            "oid": "oid-1",
            "byte_size": 42
        })
        
        doc = transformer.transform(payload, PipelineContext(config={}))
        self.assertIsNotNone(doc)
        struct_data = doc.struct_data
        self.assertEqual(struct_data["jiraTickets"], ["GEP-123", "GEP-456"])
        self.assertEqual(struct_data["contributors"], ["owner@myreddit.com"])

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_jira_and_owner_in_pr_transformer(self, mock_cache_class, mock_client_class):
        transformer = GitHubPullRequestTransformer(app_id="123", installation_id="456")
        transformer.file_transformer._resolve_repo_collaborator_emails = MagicMock(return_value=[])
        transformer.file_transformer._resolve_repo_contributors = MagicMock(return_value=["owner@myreddit.com"])
        
        payload = RawPayload(data={
            "id": "pr-1",
            "number": 42,
            "title": "Fix GEP-789 ticket",
            "body": "Resolves issue described in GEP-123.",
            "state": "OPEN",
            "repository": "MyReddit/test-repo",
            "created_at": "2026-06-09T00:00:00Z",
            "author": {"login": "author-user", "email": "author@myreddit.com"},
            "comments": [
                {"body": "This also touches CC-456.", "createdAt": "2026-06-09T01:00:00Z", "author": {"login": "commenter"}}
            ]
        })
        
        doc = transformer.transform(payload, PipelineContext(config={}))
        self.assertIsNotNone(doc)
        struct_data = doc.struct_data
        self.assertEqual(struct_data["jiraTickets"], ["CC-456", "GEP-123", "GEP-789"])
        self.assertEqual(struct_data["contributors"], ["owner@myreddit.com"])

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_jira_and_owner_in_commit_transformer(self, mock_cache_class, mock_client_class):
        transformer = GitHubCommitTransformer(app_id="123", installation_id="456")
        transformer.file_transformer._resolve_repo_collaborator_emails = MagicMock(return_value=[])
        transformer.file_transformer._resolve_repo_contributors = MagicMock(return_value=["owner@myreddit.com"])
        
        payload = RawPayload(data={
            "id": "commit-1",
            "sha": "sha-123456",
            "message": "GEP-101: Initial commit for CC-202",
            "repository": "MyReddit/test-repo",
            "committed_date": "2026-06-09T00:00:00Z",
            "author": {"name": "Author Name", "email": "author@myreddit.com", "user": {"login": "author"}}
        })
        
        doc = transformer.transform(payload, PipelineContext(config={}))
        self.assertIsNotNone(doc)
        struct_data = doc.struct_data
        self.assertEqual(struct_data["jiraTickets"], ["CC-202", "GEP-101"])
        self.assertEqual(struct_data["contributors"], ["owner@myreddit.com"])

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_jira_and_owner_in_issue_transformer(self, mock_cache_class, mock_client_class):
        transformer = GitHubIssueTransformer(app_id="123", installation_id="456")
        transformer.file_transformer._resolve_repo_collaborator_emails = MagicMock(return_value=[])
        transformer.file_transformer._resolve_repo_contributors = MagicMock(return_value=["owner@myreddit.com"])
        
        payload = RawPayload(data={
            "id": "issue-1",
            "number": 10,
            "title": "Document GEP-404 ingestion issue",
            "body": "This affects CC-505 too.",
            "state": "CLOSED",
            "repository": "MyReddit/test-repo",
            "created_at": "2026-06-09T00:00:00Z",
            "author": {"login": "author-user", "email": "author@myreddit.com"},
            "comments": [
                {"body": "Confirming fix for GEP-404.", "createdAt": "2026-06-09T01:00:00Z", "author": {"login": "commenter"}}
            ]
        })
        
        doc = transformer.transform(payload, PipelineContext(config={}))
        self.assertIsNotNone(doc)
        struct_data = doc.struct_data
        self.assertEqual(struct_data["jiraTickets"], ["CC-505", "GEP-404"])
        self.assertEqual(struct_data["contributors"], ["owner@myreddit.com"])

    @patch("src.github.transformers.file.GitHubAppClient")
    @patch("src.github.transformers.file.PipelineCache")
    def test_pluggable_identity_mapper(self, mock_cache_class, mock_client_class):
        mock_client = mock_client_class.return_value
        mock_cache = mock_cache_class.return_value
        mock_cache.get.return_value = None
        
        mock_client.execute_graphql.return_value = {
            "repository": {
                "collaborators": {
                    "nodes": [
                        {"login": "user-plugged", "email": "public@profile.com"}
                    ]
                }
            }
        }
        
        identity_mapper_config = {
            "class": "test_github_transformers.MockIdentityMapper",
            "params": {"domain": "plugged.org"}
        }
        transformer = GitHubFileTransformer(
            app_id="123",
            installation_id="456",
            identity_mapper=identity_mapper_config
        )
        
        principals = transformer._resolve_repo_collaborator_emails("MyReddit", "test-repo", PipelineContext(config={}))
        self.assertEqual(len(principals), 1)
        self.assertEqual(principals[0].user_id, "user-plugged@plugged.org")

if __name__ == "__main__":
    unittest.main()
