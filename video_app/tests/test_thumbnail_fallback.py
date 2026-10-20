from pathlib import Path
from unittest.mock import call, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from video_app.models import Video
from video_app.tasks import convert_to_hls, create_thumbnail
from video_app.tests.helpers import TempMediaMixin, create_video, make_upload
from video_app.utils import HLS_JOB_TIMEOUT

AUTO_THUMBNAIL = 'thumbnail/movie_1a2b3c4d.jpg'


def make_image(name='poster.png'):
    """Return a fake uploaded image for a custom thumbnail."""
    return SimpleUploadedFile(name, b'\x89PNG fake image', content_type='image/png')


class FallbackTestMixin(TempMediaMixin):
    """A video that already has an automatic thumbnail on disk."""

    def setUp(self):
        """Create the video with its auto thumbnail and patch the queue."""
        super().setUp()
        self.video = create_video()
        self.write_media_file(AUTO_THUMBNAIL)
        Video.objects.filter(pk=self.video.pk).update(thumbnail=AUTO_THUMBNAIL)
        self.video.refresh_from_db()
        enqueue_patcher = patch('django_rq.enqueue')
        self.enqueue = enqueue_patcher.start()
        self.addCleanup(enqueue_patcher.stop)

    def write_media_file(self, storage_name):
        """Write a dummy file into MEDIA_ROOT."""
        path = Path(self.media_root) / storage_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'data')

    def media_exists(self, storage_name):
        """Return whether a file exists in MEDIA_ROOT."""
        return (Path(self.media_root) / storage_name).exists()

    def save_after_commit(self, video):
        """Save the video and run the on_commit callbacks."""
        with self.captureOnCommitCallbacks(execute=True):
            video.save()

    def queued_tasks(self):
        """Return the task functions that were put into the queue."""
        return [enqueue_call.args[0] for enqueue_call in self.enqueue.call_args_list]


class ClearedThumbnailTests(FallbackTestMixin, TestCase):
    """Clearing the thumbnail brings back an automatic one."""

    def clear_thumbnail(self):
        """Empty the thumbnail field like the admin 'Clear' checkbox."""
        self.video.thumbnail = None
        self.save_after_commit(self.video)

    def test_cleared_thumbnail_queues_a_new_one(self):
        """Only the thumbnail job is queued, the HLS files stay as they are."""
        self.clear_thumbnail()
        self.assertEqual(self.queued_tasks(), [create_thumbnail])

    def test_cleared_thumbnail_file_is_deleted(self):
        """The image that was removed in the admin is deleted from disk."""
        self.clear_thumbnail()
        self.assertFalse(self.media_exists(AUTO_THUMBNAIL))

    def test_nothing_happens_before_commit(self):
        """Without commit no job is queued and no file deleted."""
        self.video.thumbnail = None
        with self.captureOnCommitCallbacks(execute=False):
            self.video.save()
        self.enqueue.assert_not_called()
        self.assertTrue(self.media_exists(AUTO_THUMBNAIL))

    @patch('video_app.utils.probe_duration', return_value=10.0)
    @patch('video_app.utils.run_ffmpeg')
    def test_job_creates_new_automatic_thumbnail(self, run_ffmpeg, probe):
        """Running the queued job fills the empty field again."""
        run_ffmpeg.side_effect = lambda arguments: Path(arguments[-1]).write_bytes(b'jpg')
        self.clear_thumbnail()
        create_thumbnail(self.video.pk)
        self.video.refresh_from_db()
        self.assertRegex(self.video.thumbnail.name, r'^thumbnail/movie_[0-9a-f]{8}\.jpg$')
        self.assertTrue(self.media_exists(self.video.thumbnail.name))


class CustomThumbnailTests(FallbackTestMixin, TestCase):
    """An uploaded custom thumbnail is respected."""

    def upload_custom_thumbnail(self):
        """Set a custom thumbnail like the admin upload field."""
        self.video.thumbnail = make_image()
        self.save_after_commit(self.video)

    def test_custom_thumbnail_queues_nothing(self):
        """No automatic thumbnail overwrites the custom one."""
        self.upload_custom_thumbnail()
        self.enqueue.assert_not_called()

    def test_custom_thumbnail_is_kept(self):
        """The uploaded image stays on disk and in the database."""
        self.upload_custom_thumbnail()
        self.video.refresh_from_db()
        self.assertTrue(self.video.thumbnail.name.startswith('thumbnail/poster'))
        self.assertTrue(self.media_exists(self.video.thumbnail.name))

    def test_replaced_auto_thumbnail_is_deleted(self):
        """The previous automatic image is removed from disk."""
        self.upload_custom_thumbnail()
        self.assertFalse(self.media_exists(AUTO_THUMBNAIL))

    def test_clearing_custom_thumbnail_restores_automatic_one(self):
        """Removing the custom image queues the automatic thumbnail again."""
        self.upload_custom_thumbnail()
        custom_name = self.video.thumbnail.name
        self.video.thumbnail = None
        self.save_after_commit(self.video)
        self.assertEqual(self.queued_tasks(), [create_thumbnail])
        self.assertFalse(self.media_exists(custom_name))


class NewVideoThumbnailTests(TempMediaMixin, TestCase):
    """New videos get an automatic thumbnail unless one is uploaded."""

    def create_after_commit(self, **overrides):
        """Create a video with the queue mocked and return the enqueue calls."""
        with patch('django_rq.enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                video = create_video(**overrides)
        return video, enqueue.call_args_list

    def test_new_video_without_thumbnail_gets_both_jobs(self):
        """Thumbnail first, then the HLS conversion."""
        video, calls = self.create_after_commit()
        expected = [call(create_thumbnail, video.pk),
                    call(convert_to_hls, video.pk, job_timeout=HLS_JOB_TIMEOUT)]
        self.assertEqual(calls, expected)

    def test_new_video_with_custom_thumbnail_only_converts(self):
        """An uploaded thumbnail is kept, only HLS is queued."""
        video, calls = self.create_after_commit(thumbnail=make_image())
        self.assertEqual(calls, [call(convert_to_hls, video.pk, job_timeout=HLS_JOB_TIMEOUT)])


class ReplacedVideoFileThumbnailTests(FallbackTestMixin, TestCase):
    """A new video file normally gets a new automatic thumbnail."""

    def test_new_file_regenerates_automatic_thumbnail(self):
        """The old auto thumbnail would show the old video, so a new one is made."""
        self.video.video_file = make_upload('other.mp4')
        self.save_after_commit(self.video)
        self.assertEqual(self.queued_tasks(), [create_thumbnail, convert_to_hls])

    def test_new_file_with_new_custom_thumbnail_keeps_it(self):
        """Uploading both at once keeps the custom thumbnail."""
        self.video.video_file = make_upload('other.mp4')
        self.video.thumbnail = make_image()
        self.save_after_commit(self.video)
        self.assertEqual(self.queued_tasks(), [convert_to_hls])

    def test_editing_title_queues_nothing(self):
        """Other fields do not touch the thumbnail."""
        self.video.title = 'New title'
        self.save_after_commit(self.video)
        self.enqueue.assert_not_called()
        self.assertTrue(self.media_exists(AUTO_THUMBNAIL))


class AdminClearThumbnailTests(FallbackTestMixin, TestCase):
    """The 'Clear' checkbox in the admin triggers the fallback."""

    def test_admin_clear_checkbox_restores_automatic_thumbnail(self):
        """Ticking 'Clear' and saving queues a new automatic thumbnail."""
        superuser = User.objects.create_superuser('admin', 'admin@example.com', 'pw')
        self.client.force_login(superuser)
        url = reverse('admin:video_app_video_change', args=[self.video.pk])
        data = {'title': self.video.title, 'description': self.video.description,
                'category': self.video.category, 'thumbnail-clear': 'on'}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.queued_tasks(), [create_thumbnail])
