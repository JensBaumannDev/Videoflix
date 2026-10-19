from pathlib import Path

import django_rq
from django.conf import settings

from video_app.models import Video
from video_app.utils import build_thumbnail_name, extract_thumbnail, has_source_file


def create_thumbnail(video_id):
    """Create the thumbnail of a video with FFmpeg and store its path."""
    video = Video.objects.filter(pk=video_id).first()
    if video is None or not has_source_file(video):
        return
    thumbnail_name = build_thumbnail_name(video)
    extract_thumbnail(video.video_file.path, Path(settings.MEDIA_ROOT) / thumbnail_name)
    Video.objects.filter(pk=video_id).update(thumbnail=thumbnail_name)


def queue_thumbnail(video_id):
    """Enqueue the thumbnail creation as background job for the RQ worker."""
    django_rq.enqueue(create_thumbnail, video_id)
