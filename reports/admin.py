from django.contrib import admin

from reports.models import MonthlyReport


@admin.register(MonthlyReport)
class MonthlyReportAdmin(admin.ModelAdmin):
    list_display = ('year', 'month', 'send_status', 'sent_at')
    list_filter = ('send_status', 'year')
    ordering = ('-year', '-month')
    readonly_fields = ('sent_at',)
