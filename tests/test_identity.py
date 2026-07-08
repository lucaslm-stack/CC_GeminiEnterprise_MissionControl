# Copyright 2024 Google. This software is provided as-is, without warranty or representation for any use or purpose. Your use of it is subject to your agreement with Google.

import unittest
from src.core.models import PipelineContext
from src.github.identity import UnifiedRepositoryIdentityMapper

class TestIdentityMappers(unittest.TestCase):



    def test_github_commit_mining_identity_mapper(self):
        from src.github.identity import GitHubCommitMiningIdentityMapper
        from unittest.mock import MagicMock
        
        mock_client = MagicMock()
        
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
        mock_client.execute_graphql.return_value = commit_history_response
        
        mapper = GitHubCommitMiningIdentityMapper(client=mock_client)
        emails = mapper.map_identities(["user-a", "user-b"], "MyReddit/test-repo", PipelineContext(config={}))
        self.assertEqual(emails, ["user-a@myreddit.com", None])

    def test_unified_identity_mapper(self):
        mapper = UnifiedRepositoryIdentityMapper(config_file="tests/test_data/test_unified_acl.yaml")
        
        # 1. Test specific mapping with groups and explicit emails
        emails_repo1 = mapper.map_identities([], "MyReddit/repo-1", PipelineContext(config={}))
        self.assertEqual(emails_repo1, ["email1@email.com", "email2@email.com", "extra@email.com"])
        
        # Test case-insensitivity
        emails_repo1_lower = mapper.map_identities([], "myreddit/repo-1", PipelineContext(config={}))
        self.assertEqual(emails_repo1_lower, ["email1@email.com", "email2@email.com", "extra@email.com"])
        
        # 2. Test public mapping
        emails_public = mapper.map_identities([], "MyReddit/repo-public", PipelineContext(config={}))
        self.assertEqual(emails_public, ["allUsers"])
        
        # 3. Test wildcard fallback mapping
        emails_fallback = mapper.map_identities([], "MyReddit/some-other-repo", PipelineContext(config={}))
        self.assertEqual(emails_fallback, ["default-audit@email.com"])

    def test_domain_pass_through_identity_mapper(self):
        from src.github.identity import DomainPassThroughIdentityMapper
        
        # Test valid domain
        mapper = DomainPassThroughIdentityMapper(domain="altostrat.com")
        emails = mapper.map_identities(["sandra_martinez", "admin", None], "MyReddit/test-repo", PipelineContext(config={}))
        self.assertEqual(emails, ["sandra_martinez@altostrat.com", "admin@altostrat.com", None])
        
        # Test leading @ strip
        mapper_with_at = DomainPassThroughIdentityMapper(domain="@altostrat.com")
        emails_with_at = mapper_with_at.map_identities(["admin"], "MyReddit/test-repo", PipelineContext(config={}))
        self.assertEqual(emails_with_at, ["admin@altostrat.com"])

        # Test empty domain raise exception
        with self.assertRaises(ValueError):
            DomainPassThroughIdentityMapper(domain="   ")

if __name__ == "__main__":
    unittest.main()
