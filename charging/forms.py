from decimal import Decimal

from django import forms

from .models import Tariff


class TariffForm(forms.ModelForm):
    class Meta:
        model = Tariff
        fields = ["valid_from", "energy_price_ct_per_kwh", "base_fee_eur_per_month"]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_energy_price_ct_per_kwh(self):
        value = self.cleaned_data["energy_price_ct_per_kwh"]
        if value < Decimal("0"):
            raise forms.ValidationError("Energy price must not be negative.")
        return value

    def clean_base_fee_eur_per_month(self):
        value = self.cleaned_data["base_fee_eur_per_month"]
        if value < Decimal("0"):
            raise forms.ValidationError("Base fee must not be negative.")
        return value
