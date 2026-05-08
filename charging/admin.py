from django.contrib import admin

from charging.models import ChargingSession, MonthlyHouseUsage, Tariff


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ('valid_from', 'energy_price_ct_per_kwh', 'base_fee_eur')
    ordering = ('-valid_from',)


@admin.register(ChargingSession)
class ChargingSessionAdmin(admin.ModelAdmin):
    list_display = ('start', 'end', 'kwh', 'meter_start', 'meter_end')
    list_filter = ('start',)
    search_fields = ('note',)
    date_hierarchy = 'start'


@admin.register(MonthlyHouseUsage)
class MonthlyHouseUsageAdmin(admin.ModelAdmin):
    list_display = ('year', 'month', 'household_kwh')
    list_filter = ('year',)
    ordering = ('-year', '-month')
