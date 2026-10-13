from django.contrib import admin

from .models import Video


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    """Admin view for uploading and managing videos."""

    list_display = ['title', 'category', 'created_at']
    list_filter = ['category']
    search_fields = ['title', 'description']
    readonly_fields = ['created_at']
