from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from video_app.models import Video
from video_app.tasks import queue_video_processing
from video_app.utils import delete_media_file, delete_video_files, get_stored_video_file


@receiver(pre_save, sender=Video)
def remember_previous_video_file(sender, instance, **kwargs):
    """Note the video file stored before this save (None for a new video)."""
    instance.previous_video_file = get_stored_video_file(instance)


@receiver(post_save, sender=Video)
def handle_new_video_file(sender, instance, **kwargs):
    """After the commit, process a new video file and delete the replaced one."""
    previous_file = instance.previous_video_file
    if previous_file == instance.video_file.name:
        return
    transaction.on_commit(lambda: queue_video_processing(instance.pk))
    if previous_file:
        transaction.on_commit(lambda: delete_media_file(previous_file))


@receiver(post_delete, sender=Video)
def delete_files_of_deleted_video(sender, instance, **kwargs):
    """After the commit, delete original, thumbnail and HLS files of the video."""
    video_id = instance.pk
    storage_names = [instance.video_file.name, instance.thumbnail.name]
    transaction.on_commit(lambda: delete_video_files(video_id, storage_names))
