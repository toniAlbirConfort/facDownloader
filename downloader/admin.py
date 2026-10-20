from django.contrib import admin
from django import forms

from .crypto import encrypt
from .models import AuditEvent, DownloadBatch, DownloadItem, InvoiceCandidate, InvoiceDocument, InvoiceSearch, Mailbox, MailboxCredential, ProviderCredential


class MailboxCredentialInline(admin.StackedInline):
    model = MailboxCredential
    extra = 1
    max_num = 1
    fields = ("host", "port", "username", "password", "use_ssl", "folder")

    class Form(forms.ModelForm):
        password = forms.CharField(
            required=False,
            widget=forms.PasswordInput(render_value=False),
            help_text="Se guarda cifrada. Déjalo vacío para conservar la contraseña actual.",
        )

        class Meta:
            model = MailboxCredential
            fields = ("host", "port", "username", "password", "use_ssl", "folder")

        def clean(self):
            cleaned = super().clean()
            if not self.instance.pk and not cleaned.get("password"):
                self.add_error("password", "La contraseña es obligatoria al crear la configuración IMAP.")
            return cleaned

        def save(self, commit=True):
            instance = super().save(commit=False)
            password = self.cleaned_data.get("password")
            if password:
                instance.password_ciphertext = encrypt(password)
            if commit:
                instance.save()
            return instance

    form = Form


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "is_active")
    search_fields = ("name", "address")
    inlines = (MailboxCredentialInline,)


@admin.register(ProviderCredential)
class ProviderCredentialAdmin(admin.ModelAdmin):
    class Form(forms.ModelForm):
        secret = forms.CharField(
            required=False,
            widget=forms.PasswordInput(render_value=False),
            help_text="Se guarda cifrado. Para Repsol, introduce un CIF/NIF por línea para habilitar fallbacks.",
        )

        class Meta:
            model = ProviderCredential
            fields = ("provider", "secret")

        def save(self, commit=True):
            instance = super().save(commit=False)
            secret = self.cleaned_data.get("secret")
            if secret:
                instance.secret_ciphertext = encrypt(secret)
            if commit:
                instance.save()
            return instance

    form = Form
    list_display = ("provider", "updated_at")
    readonly_fields = ("secret_ciphertext",)


@admin.register(InvoiceSearch)
class InvoiceSearchAdmin(admin.ModelAdmin):
    list_display = ("id", "mailbox", "start_date", "end_date", "status", "total_found", "created_at")
    list_filter = ("status", "mailbox")


@admin.register(InvoiceCandidate)
class InvoiceCandidateAdmin(admin.ModelAdmin):
    list_display = ("provider", "invoice_type", "sender", "subject", "received_at")
    search_fields = ("sender", "subject", "internet_message_id")


@admin.register(InvoiceDocument)
class InvoiceDocumentAdmin(admin.ModelAdmin):
    list_display = ("file_name", "provider", "invoice_type", "status", "size_bytes", "created_at")
    list_filter = ("status", "provider", "invoice_type")
    search_fields = ("file_name", "sha256")


admin.site.register(DownloadBatch)
admin.site.register(DownloadItem)
admin.site.register(AuditEvent)

