from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from video_app.api.serializers import VideoListSerializer
from video_app.models import Video


class VideoListView(ListAPIView):
    """List all videos, newest first, for logged-in users."""

    queryset = Video.objects.order_by('-created_at', '-id')
    serializer_class = VideoListSerializer
    permission_classes = [IsAuthenticated]
