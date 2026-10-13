from django.core.validators import FileExtensionValidator
from django.db import models

ALLOWED_VIDEO_EXTENSIONS = ['mp4', 'mov', 'mkv', 'webm', 'avi']


class Video(models.Model):
    """A video uploaded via the admin panel and streamed as HLS."""

    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=100)
    video_file = models.FileField(
        upload_to='videos/',
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_VIDEO_EXTENSIONS)],
    )
    thumbnail = models.FileField(upload_to='thumbnail/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Newest videos first, as required for the dashboard."""

        ordering = ['-created_at']

    def __str__(self):
        """Return the title for admin and shell display."""
        return self.title
