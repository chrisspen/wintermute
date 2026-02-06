"""Infrastructure-related models for Wintermute."""

import uuid
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class VMTarget(models.Model):
    """Virtual machine target for agent sessions."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    host = models.CharField(max_length=255)
    user = models.CharField(max_length=255)
    port = models.IntegerField()
    required_reserve_memory_gb = models.FloatField(default=0.0)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "vm_targets"
        verbose_name = "VM Target"
        verbose_name_plural = "VM Targets"

    def __str__(self):
        return f"{self.name} ({self.user}@{self.host}:{self.port})"
