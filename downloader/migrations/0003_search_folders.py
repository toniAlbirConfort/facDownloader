from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("downloader", "0002_mailboxcredential")]

    operations = [
        migrations.AddField(
            model_name="invoicesearch",
            name="folders",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="invoicecandidate",
            name="folder",
            field=models.CharField(default="INBOX", max_length=255),
        ),
        migrations.RemoveConstraint(
            model_name="invoicecandidate",
            name="unique_search_message_provider",
        ),
        migrations.AddConstraint(
            model_name="invoicecandidate",
            constraint=models.UniqueConstraint(
                fields=("search", "folder", "message_id", "provider"),
                name="unique_search_folder_message_provider",
            ),
        ),
    ]
