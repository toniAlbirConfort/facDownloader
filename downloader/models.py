from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


class Mailbox(models.Model):
    name = models.CharField(max_length=120)
    address = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} <{self.address}>"


class MailboxCredential(models.Model):
    mailbox = models.OneToOneField(Mailbox, on_delete=models.CASCADE, related_name="imap_credential")
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=993)
    username = models.CharField(max_length=255)
    password_ciphertext = models.TextField()
    use_ssl = models.BooleanField(default=True)
    folder = models.CharField(max_length=255, default="INBOX")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"IMAP {self.mailbox.address}"


class ProviderCredential(models.Model):
    provider = models.CharField(max_length=40, unique=True)
    secret_ciphertext = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class InvoiceSearch(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Ejecutando"
        COMPLETED = "completed", "Completada"
        FAILED = "failed", "Fallida"

    requested_by = models.ForeignKey(User, on_delete=models.PROTECT)
    mailbox = models.ForeignKey(Mailbox, on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    invoice_types = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    total_found = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class InvoiceCandidate(models.Model):
    search = models.ForeignKey(InvoiceSearch, on_delete=models.CASCADE, related_name="candidates")
    message_id = models.CharField(max_length=500)
    internet_message_id = models.CharField(max_length=500, blank=True)
    provider = models.CharField(max_length=40)
    invoice_type = models.CharField(max_length=20)
    sender = models.EmailField()
    subject = models.TextField()
    received_at = models.DateTimeField(null=True, blank=True)
    source_links = models.JSONField(default=list)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["search", "message_id", "provider"], name="unique_search_message_provider")]
        ordering = ["-received_at", "-created_at"]


class InvoiceDocument(models.Model):
    class Status(models.TextChoices):
        DOWNLOADED = "downloaded", "Descargada"
        NEEDS_REVIEW = "needs_review", "Requiere revisión"
        FAILED = "failed", "Fallida"

    candidate = models.ForeignKey(InvoiceCandidate, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    provider = models.CharField(max_length=40)
    invoice_type = models.CharField(max_length=20)
    file_name = models.CharField(max_length=255)
    file_path = models.TextField(blank=True)
    sha256 = models.CharField(max_length=64, unique=True, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class DownloadBatch(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Ejecutando"
        COMPLETED = "completed", "Completada"
        FAILED = "failed", "Fallida"

    requested_by = models.ForeignKey(User, on_delete=models.PROTECT)
    search = models.ForeignKey(InvoiceSearch, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    zip_path = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class DownloadItem(models.Model):
    batch = models.ForeignKey(DownloadBatch, on_delete=models.CASCADE, related_name="items")
    candidate = models.ForeignKey(InvoiceCandidate, on_delete=models.PROTECT)
    document = models.ForeignKey(InvoiceDocument, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, default="queued")
    error = models.TextField(blank=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

