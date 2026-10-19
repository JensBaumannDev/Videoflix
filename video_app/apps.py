from django.apps import AppConfig


class VideoAppConfig(AppConfig):
    """App for uploading, converting and streaming videos."""

    name = 'video_app'

    def ready(self):
        """Import the signals module so its receivers get connected."""
        import video_app.signals
