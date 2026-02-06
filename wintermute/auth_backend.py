"""Custom authentication backend for Wintermute users."""

import base64
import hashlib
import hmac
from django.contrib.auth.backends import BaseBackend
from wintermute.models import User


def _hash_password(password: str, salt: bytes) -> str:
    """Hash password using scrypt (matches original Wintermute implementation)."""
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return base64.b64encode(derived).decode("ascii")


def _verify_password(password: str, salt_b64: str, stored_hash: str) -> bool:
    """Verify password using scrypt (matches original Wintermute implementation)."""
    salt = base64.b64decode(salt_b64.encode("ascii"))
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


class WintermuteAuthBackend(BaseBackend):
    """Authentication backend that handles both legacy (scrypt + salt) and Django passwords."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        # If user has Django-style password, use that
        if user.password.startswith('pbkdf2_') or user.password.startswith('bcrypt'):
            if user.check_password(password):
                return user
            return None

        # Legacy scrypt password verification
        if user.salt:
            if _verify_password(password, user.salt, user.password):
                # Migrate to Django password format on successful login
                user.set_password(password)
                user.salt = ''
                user.save()
                return user

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
