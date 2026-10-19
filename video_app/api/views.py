from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from video_app.api.serializers import VideoListSerializer
from video_app.models import Video
from video_app.utils import (
    HLS_PLAYLIST_NAME,
    MANIFEST_CONTENT_TYPE,
    SEGMENT_CONTENT_TYPE,
    build_file_response,
    find_hls_file,
    find_hls_segment,
)


class VideoListView(ListAPIView):
    """List all videos, newest first, for logged-in users."""

    queryset = Video.objects.order_by('-created_at', '-id')
    serializer_class = VideoListSerializer
    permission_classes = [IsAuthenticated]


class VideoManifestView(APIView):
    """Serve the HLS playlist (index.m3u8) of one resolution."""

    permission_classes = [IsAuthenticated]

    def get(self, request, movie_id, resolution):
        """Return the playlist file or 404 if video or playlist is missing."""
        manifest_path = find_hls_file(movie_id, resolution, HLS_PLAYLIST_NAME)
        return build_file_response(manifest_path, MANIFEST_CONTENT_TYPE)


class VideoSegmentView(APIView):
    """Serve one HLS segment like 000.ts of one resolution."""

    permission_classes = [IsAuthenticated]

    def get(self, request, movie_id, resolution, segment):
        """Return the segment file or 404 if video or segment is missing."""
        segment_path = find_hls_segment(movie_id, resolution, segment)
        return build_file_response(segment_path, SEGMENT_CONTENT_TYPE)
