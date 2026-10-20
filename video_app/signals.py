from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from video_app.models import Video
from video_app.tasks import queue_hls_conversion, queue_thumbnail
from video_app.utils import (
    clear_video_list_cache,
    delete_media_file,
    delete_video_files,
    detect_file_changes,
    get_stored_files,
    needs_auto_thumbnail,
)


@receiver(pre_save, sender=Video)
def remember_previous_files(sender, instance, **kwargs):
    """Note video file and thumbnail stored before this save (None for a new video)."""
    instance.previous_files = get_stored_files(instance)


@receiver(post_save, sender=Video)
def queue_jobs_for_changed_files(sender, instance, **kwargs):
    """After the commit, create a missing thumbnail and convert a new video file."""
    file_changed, thumbnail_changed = detect_file_changes(instance)
    if needs_auto_thumbnail(instance, file_changed, thumbnail_changed):
        transaction.on_commit(lambda: queue_thumbnail(instance.pk))
    if file_changed:
        transaction.on_commit(lambda: queue_hls_conversion(instance.pk))


@receiver(post_save, sender=Video)
def delete_replaced_files(sender, instance, **kwargs):
    """After the commit, delete a replaced or cleared video file or thumbnail."""
    current_names = [instance.video_file.name, instance.thumbnail.name]
    for previous_name, current_name in zip(instance.previous_files, current_names):
        if previous_name and previous_name != current_name:
            transaction.on_commit(lambda name=previous_name: delete_media_file(name))


@receiver(post_delete, sender=Video)
def delete_files_of_deleted_video(sender, instance, **kwargs):
    """After the commit, delete original, thumbnail and HLS files of the video."""
    video_id = instance.pk
    storage_names = [instance.video_file.name, instance.thumbnail.name]
    transaction.on_commit(lambda: delete_video_files(video_id, storage_names))


@receiver([post_save, post_delete], sender=Video)
def clear_cached_video_list(sender, **kwargs):
    """After the commit, drop the cached video list so changes show up."""
    transaction.on_commit(clear_video_list_cache)
