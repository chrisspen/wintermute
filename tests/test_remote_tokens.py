"""Unit tests for RemoteToken model and database methods."""

import uuid

import pytest

from wintermute.models import RemoteToken
from wintermute.services.database import Database


@pytest.mark.django_db(transaction=True)
class TestRemoteToken:

    def setup_method(self) -> None:
        self.db = Database(":memory:") # Path ignored, uses Django's test DB

    def teardown_method(self) -> None:
        RemoteToken.objects.all().delete()

    def test_insert_and_get_remote_token(self) -> None:
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="github",
            token="ghp_test123",
            note="Test token",
            user_id="12345",
            user_login="testuser",
        )
        token = self.db.get_remote_token(token_id)
        assert token is not None
        assert token.id == token_id
        assert token.provider == "github"
        assert token.token == "ghp_test123"
        assert token.note == "Test token"
        assert token.user_id == "12345"
        assert token.user_login == "testuser"

    def test_list_remote_tokens(self) -> None:
        # Insert GitHub token
        github_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            github_id,
            provider="github",
            token="ghp_test",
            note="GitHub token",
        )
        # Insert GitLab token
        gitlab_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            gitlab_id,
            provider="gitlab",
            token="glpat_test",
            note="GitLab token",
        )

        # List all tokens
        all_tokens = self.db.list_remote_tokens()
        assert len(all_tokens) == 2

        # List only GitHub tokens
        github_tokens = self.db.list_remote_tokens(provider="github")
        assert len(github_tokens) == 1
        assert github_tokens[0].provider == "github"

        # List only GitLab tokens
        gitlab_tokens = self.db.list_remote_tokens(provider="gitlab")
        assert len(gitlab_tokens) == 1
        assert gitlab_tokens[0].provider == "gitlab"

    def test_update_remote_token(self) -> None:
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="github",
            token="ghp_old",
            note="Old note",
        )

        self.db.update_remote_token(
            token_id,
            token="ghp_new",
            note="New note",
            user_login="newuser",
        )

        token = self.db.get_remote_token(token_id)
        assert token.token == "ghp_new"
        assert token.note == "New note"
        assert token.user_login == "newuser"

    def test_update_remote_token_provider(self) -> None:
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="github",
            token="test_token",
        )

        self.db.update_remote_token(token_id, provider="gitlab")

        token = self.db.get_remote_token(token_id)
        assert token.provider == "gitlab"

    def test_delete_remote_token(self) -> None:
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="github",
            token="ghp_test",
        )

        assert self.db.get_remote_token(token_id) is not None

        self.db.delete_remote_token(token_id)

        assert self.db.get_remote_token(token_id) is None

    def test_get_nonexistent_token(self) -> None:
        token = self.db.get_remote_token("nonexistent")
        assert token is None

    def test_legacy_github_token_methods(self) -> None:
        """Test that legacy GitHub token methods work with unified table."""
        token_id = str(uuid.uuid4())
        self.db.insert_github_token(
            token_id,
            token="ghp_legacy",
            note="Legacy GitHub",
            user_id="123",
            user_login="legacyuser",
        )

        # Should be retrievable via legacy method
        github_token = self.db.get_github_token(token_id)
        assert github_token is not None
        assert github_token.token == "ghp_legacy"

        # Should also be in unified list
        remote_token = self.db.get_remote_token(token_id)
        assert remote_token is not None
        assert remote_token.provider == "github"

        # Legacy list should work
        github_tokens = self.db.list_github_tokens()
        assert len(github_tokens) == 1

        # Update via legacy method
        self.db.update_github_token(token_id, note="Updated note")
        updated = self.db.get_github_token(token_id)
        assert updated.note == "Updated note"

        # Delete via legacy method
        self.db.delete_github_token(token_id)
        assert self.db.get_github_token(token_id) is None

    def test_legacy_gitlab_token_methods(self) -> None:
        """Test that legacy GitLab token methods work with unified table."""
        token_id = str(uuid.uuid4())
        self.db.insert_gitlab_token(
            token_id,
            token="glpat_legacy",
            note="Legacy GitLab",
            user_id="456",
            user_login="gitlabuser",
        )

        # Should be retrievable via legacy method
        gitlab_token = self.db.get_gitlab_token(token_id)
        assert gitlab_token is not None
        assert gitlab_token.token == "glpat_legacy"

        # Should also be in unified list
        remote_token = self.db.get_remote_token(token_id)
        assert remote_token is not None
        assert remote_token.provider == "gitlab"

        # Legacy list should work
        gitlab_tokens = self.db.list_gitlab_tokens()
        assert len(gitlab_tokens) == 1

        # Update via legacy method
        self.db.update_gitlab_token(token_id, note="Updated GitLab note")
        updated = self.db.get_gitlab_token(token_id)
        assert updated.note == "Updated GitLab note"

        # Delete via legacy method
        self.db.delete_gitlab_token(token_id)
        assert self.db.get_gitlab_token(token_id) is None

    def test_legacy_get_github_token_wrong_provider(self) -> None:
        """Test that get_github_token returns None for gitlab tokens."""
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="gitlab",
            token="glpat_test",
        )

        # Should return None when asking for GitHub token
        github_token = self.db.get_github_token(token_id)
        assert github_token is None

    def test_legacy_get_gitlab_token_wrong_provider(self) -> None:
        """Test that get_gitlab_token returns None for github tokens."""
        token_id = str(uuid.uuid4())
        self.db.insert_remote_token(
            token_id,
            provider="github",
            token="ghp_test",
        )

        # Should return None when asking for GitLab token
        gitlab_token = self.db.get_gitlab_token(token_id)
        assert gitlab_token is None


if __name__ == "__main__":
    pytest.main([__file__])
