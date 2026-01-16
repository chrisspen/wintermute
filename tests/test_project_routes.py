"""Tests for project web routes."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


class ProjectRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Set a known secret for session signing
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        self.app = create_app(self.db)
        self.client = TestClient(self.app)
        # Create a test user with properly hashed password
        password = "testpass"
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        password_hash = base64.b64encode(
            hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=2**14,
                r=8,
                p=1,
            )
        ).decode("ascii")
        self.db.insert_user(
            user_id=str(uuid.uuid4()),
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login to get session cookie
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_project_create_has_mirror_repo_path_base(self) -> None:
        """Project create page should include mirror_repo_path_base for JS auto-fill."""
        response = self.client.get("/ui/projects/create")
        self.assertEqual(response.status_code, 200)
        # Check that mirror_repo_path_base is in the response (used by JS)
        self.assertIn("mirrorRepoPathBase", response.text)

    def test_project_create_uses_env_var_for_mirror_path(self) -> None:
        """Mirror repo path base should respect WINTERMUTE_MIRROR_REPO_PATH_BASE env var."""
        custom_path = "/custom/mirror/path"
        with patch.dict(os.environ, {"WINTERMUTE_MIRROR_REPO_PATH_BASE": custom_path}):
            response = self.client.get("/ui/projects/create")
            self.assertEqual(response.status_code, 200)
            self.assertIn(custom_path, response.text)

    def test_project_edit_has_mirror_repo_path_base(self) -> None:
        """Project edit page should include mirror_repo_path_base for JS auto-fill."""
        # Create a project first
        self.db.insert_project("test-proj", "Test Project", "test-proj", None)

        response = self.client.get("/ui/projects/test-proj/edit")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mirrorRepoPathBase", response.text)

    def test_project_edit_uses_env_var_for_mirror_path(self) -> None:
        """Edit page mirror repo path base should respect env var."""
        custom_path = "/my/custom/repos"
        self.db.insert_project("test-proj-2", "Test Project 2", "test-proj-2", None)

        with patch.dict(os.environ, {"WINTERMUTE_MIRROR_REPO_PATH_BASE": custom_path}):
            response = self.client.get("/ui/projects/test-proj-2/edit")
            self.assertEqual(response.status_code, 200)
            self.assertIn(custom_path, response.text)

    def test_project_create_default_mirror_path(self) -> None:
        """Project create uses default /home/user/git when env var not set."""
        # Ensure env var is not set
        env_copy = os.environ.copy()
        if "WINTERMUTE_MIRROR_REPO_PATH_BASE" in env_copy:
            del env_copy["WINTERMUTE_MIRROR_REPO_PATH_BASE"]
        with patch.dict(os.environ, env_copy, clear=True):
            # Need fresh app to pick up cleared env
            response = self.client.get("/ui/projects/create")
            self.assertEqual(response.status_code, 200)
            self.assertIn("/home/user/git", response.text)


if __name__ == "__main__":
    unittest.main()
