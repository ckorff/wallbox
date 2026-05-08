from __future__ import annotations

from django.db import models
from django.db.models import CheckConstraint, Q


def report_pdf_path(instance: 'MonthlyReport', filename: str) -> str:
    return f'reports/{instance.year}-{instance.month:02d}.pdf'


class MonthlyReport(models.Model):
    """A generated monthly cost report and its delivery status."""

    class SendStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    pdf = models.FileField(upload_to=report_pdf_path, blank=True)
    send_status = models.CharField(
        max_length=10,
        choices=SendStatus.choices,
        default=SendStatus.PENDING,
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(fields=['year', 'month'], name='monthlyreport_year_month_unique'),
            CheckConstraint(
                check=Q(month__gte=1) & Q(month__lte=12),
                name='monthlyreport_month_range',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.year}-{self.month:02d} ({self.send_status})'
