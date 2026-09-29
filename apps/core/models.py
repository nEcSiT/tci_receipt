import uuid
from django.db import models, transaction


class TimeStampedModel(models.Model):
    """Abstract model providing automatic timestamp tracking."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDBaseModel(TimeStampedModel):
    """Abstract base model with UUID primary key and timestamp tracking."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BusinessSequence(models.Model):
    """
    Tracks sequential counters for human-readable business IDs to guarantee concurrency safety
    and prevent generating IDs by row counting.
    """
    sequence_type = models.CharField(max_length=50, unique=True)
    last_value = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business Sequence"
        verbose_name_plural = "Business Sequences"

    def __str__(self):
        return f"{self.sequence_type}: {self.last_value}"

    @classmethod
    def get_next_value(cls, sequence_type: str, initial_value: int = 1) -> int:
        """
        Safely increments and returns the next sequence value inside a database transaction lock.
        """
        with transaction.atomic():
            seq, created = cls.objects.select_for_update().get_or_create(
                sequence_type=sequence_type,
                defaults={"last_value": initial_value - 1}
            )
            seq.last_value += 1
            seq.save(update_fields=["last_value", "updated_at"])
            return seq.last_value
