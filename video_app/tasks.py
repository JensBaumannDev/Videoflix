from pathlib import Path

import django_rq
from django.conf import settings

from video_app.models import Video
from video_app.utils import (
    HLS_JOB_TIMEOUT,
    HLS_RESOLUTIONS,
    build_thumbnail_name,
    convert_rendition,
    delete_media_file,
    extract_thumbnail,
    has_source_file,
    remove_orphaned_hls,
)


def get_processable_video(video_id):
    """Return the video if it exists and its file is on disk, else None."""
    video = Video.objects.filter(pk=video_id).first()
    if video is None or not has_source_file(video):
        return None
    return video


def create_thumbnail(video_id):
    """Create the thumbnail of a video with FFmpeg and store its path."""
    video = get_processable_video(video_id)
    if video is None:
        return
    old_thumbnail = video.thumbnail.name
    thumbnail_name = build_thumbnail_name(video)
    extract_thumbnail(video.video_file.path, Path(settings.MEDIA_ROOT) / thumbnail_name)
    Video.objects.filter(pk=video_id).update(thumbnail=thumbnail_name)
    if old_thumbnail != thumbnail_name:
        delete_media_file(old_thumbnail)


def convert_to_hls(video_id):
    """Convert a video into HLS playlists and segments for all resolutions."""
    video = get_processable_video(video_id)
    if video is None:
        return
    for resolution, height in HLS_RESOLUTIONS.items():
        convert_rendition(video, resolution, height)
    remove_orphaned_hls(video_id)


def queue_video_processing(video_id):
    """Enqueue thumbnail creation and HLS conversion for the RQ worker."""
    django_rq.enqueue(create_thumbnail, video_id)
    django_rq.enqueue(convert_to_hls, video_id, job_timeout=HLS_JOB_TIMEOUT)
