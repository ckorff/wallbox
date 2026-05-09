from decimal import Decimal

from django import forms

from .models import Tariff


class TariffForm(forms.ModelForm):
    valid_from = forms.DateField(
        input_formats=["%d.%m.%Y"],
        widget=forms.DateInput(
            format="%d.%m.%Y",
            attrs={"placeholder": "DD.MM.YYYY", "inputmode": "numeric"},
        ),
        help_text="Format: DD.MM.YYYY (e.g. 01.05.2026)",
    )

    class Meta:
        model = Tariff
        fields = ["valid_from", "energy_price_ct_per_kwh"]

    def clean_energy_price_ct_per_kwh(self):
        value = self.cleaned_data["energy_price_ct_per_kwh"]
        if value < Decimal("0"):
            raise forms.ValidationError("Energy price must not be negative.")
        return value
