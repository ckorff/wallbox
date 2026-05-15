from decimal import Decimal

from django import forms

from .models import AppSettings, Tariff


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


class WallboxApiForm(forms.ModelForm):
    keba_api_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the current password.",
    )

    class Meta:
        model = AppSettings
        fields = ["keba_api_username", "keba_api_password"]

    def __init__(self, *args, **kwargs):
        # Always bind to the singleton row so other fields (e.g. the
        # report recipient email) are preserved through save().
        kwargs.setdefault("instance", AppSettings.current())
        super().__init__(*args, **kwargs)

    def clean_keba_api_password(self):
        value = self.cleaned_data.get("keba_api_password", "")
        if not value:
            # Blank submission = keep what's already stored.
            return AppSettings.current().keba_api_password
        return value


class ReportRecipientForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = ["report_recipient_email"]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("instance", AppSettings.current())
        super().__init__(*args, **kwargs)
