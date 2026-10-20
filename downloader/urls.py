from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("busqueda/", views.search_page, name="search-page"),
    path("biblioteca/", views.library_page, name="library-page"),
    path("api/mailboxes/<int:mailbox_id>/folders", views.mailbox_folders, name="mailbox-folders"),
    path("api/searches", views.create_search, name="create-search"),
    path("api/searches/<int:search_id>", views.search_detail, name="search-detail"),
    path("api/searches/<int:search_id>/results", views.search_results, name="search-results"),
    path("api/searches/<int:search_id>/download", views.start_download, name="start-download"),
    path("api/downloads/<int:batch_id>", views.download_status, name="download-status"),
    path("api/downloads/<int:batch_id>/file", views.download_zip, name="download-zip"),
    path("api/documents", views.document_list, name="document-list"),
    path("api/documents/<int:document_id>/download", views.document_download, name="document-download"),
    path("api/documents/<int:document_id>", views.document_delete, name="document-delete"),
]

