from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from video_app.models import Video
from video_app.tasks import queue_thumbnail
from video_app.utils import video_file_changed


@receiver(pre_save, sender=Video)
def remember_video_file_change(sender, instance, **kwargs):
    """Note before saving whether a new video file was uploaded."""
    instance.video_file_changed = video_file_changed(instance)


@receiver(post_save, sender=Video)
def queue_thumbnail_for_new_file(sender, instance, **kwargs):
    """Queue the thumbnail job after the commit if the video file is new."""
    if instance.video_file_changed:
        transaction.on_commit(lambda: queue_thumbnail(instance.pk))
