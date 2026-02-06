"""Authentication and user-related models for Wintermute."""

import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Manager for Wintermute User model."""

    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError('The Username field must be set')
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, password, **extra_fields)


def generate_uuid():
    return str(uuid.uuid4())


class User(AbstractBaseUser):
    """Wintermute user model - used for admin authentication and throughout the app."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    username = models.CharField(max_length=255, unique=True)
    password = models.CharField(max_length=255, db_column='password_hash')
    salt = models.CharField(max_length=255, blank=True, default='', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Django auth fields
    last_login = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=True)
    is_superuser = models.BooleanField(default=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.username

    def has_perm(self, perm, obj=None):
        return self.is_superuser

    def has_perms(self, perm_list, obj=None):
        """Return True if the user has all specified permissions."""
        return all(self.has_perm(perm, obj) for perm in perm_list)

    def has_module_perms(self, app_label):
        return self.is_superuser


class ColumnPreference(models.Model):
    """User's column visibility preferences for UI tables."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    user_id = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    columns_json = models.TextField() # JSON list of column names
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "ui_column_preferences"
        constraints = [models.UniqueConstraint(fields=["user_id", "model"], name="uq_ui_column_preferences_user_model")]
        verbose_name = "Column Preference"
        verbose_name_plural = "Column Preferences"

    def __str__(self):
        return f"{self.user_id}:{self.model}"


class Credential(models.Model):
    """Generic credential storage (legacy, may not be used)."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    provider = models.CharField(max_length=255)
    reference = models.CharField(max_length=255)
    note = models.TextField(null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "credentials"
        verbose_name = "Credential"
        verbose_name_plural = "Credentials"

    def __str__(self):
        return f"{self.name} ({self.provider})"
