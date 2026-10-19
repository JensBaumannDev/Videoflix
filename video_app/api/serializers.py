from rest_framework import serializers

from video_app.models import Video


class VideoListSerializer(serializers.ModelSerializer):
    """Serialize a video for the dashboard with an absolute thumbnail URL."""

    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        """Expose exactly the fields of the endpoint doc."""

        model = Video
        fields = ['id', 'created_at', 'title', 'description', 'thumbnail_url', 'category']

    def get_thumbnail_url(self, video):
        """Return the absolute thumbnail URL, or None while it is not created yet."""
        if not video.thumbnail:
            return None
        return self.context['request'].build_absolute_uri(video.thumbnail.url)
