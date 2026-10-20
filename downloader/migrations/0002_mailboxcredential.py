Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("downloader", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailboxCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("host", models.CharField(max_length=255)),
                ("port", models.PositiveIntegerField(default=993)),
                ("username", models.CharField(max_length=255)),
                ("password_ciphertext", models.TextField()),
                ("use_ssl", models.BooleanField(default=True)),
                ("folder", models.CharField(default="INBOX", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mailbox", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="imap_credential", to="downloader.mailbox")),
            ],
        ),
    ]

