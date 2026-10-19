from unittest.mock import call, patch

from django.test import TestCase

from video_app.tasks import HLS_JOB_TIMEOUT, convert_to_hls, create_thumbnail
from video_app.tests.helpers import TempMediaMixin, create_video, make_upload


@patch('django_rq.enqueue')
class VideoProcessingSignalTests(TempMediaMixin, TestCase):
    """Saving a video queues thumbnail and HLS jobs only when needed."""

    def expected_calls(self, video):
        """Return the two enqueue calls a new video file must produce."""
        return [call(create_thumbnail, video.pk),
                call(convert_to_hls, video.pk, job_timeout=HLS_JOB_TIMEOUT)]

    def test_new_video_queues_thumbnail_and_hls(self, enqueue):
        """A new video queues the thumbnail first, then the HLS conversion."""
        video = self.create_video_after_commit()
        self.assertEqual(enqueue.call_args_list, self.expected_calls(video))

    def test_hls_job_gets_long_timeout(self, enqueue):
        """Converting long films needs more than the default 900 seconds."""
        self.assertGreater(HLS_JOB_TIMEOUT, 900)

    def test_jobs_are_queued_only_after_commit(self, enqueue):
        """The jobs wait for the commit so the worker finds the row."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            create_video()
        enqueue.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_changing_other_fields_queues_nothing(self, enqueue):
        """Editing title, description or category needs no processing."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        video.title = 'New title'
        with self.captureOnCommitCallbacks(execute=True):
            video.save()
        enqueue.assert_not_called()

    def test_replacing_the_video_file_queues_again(self, enqueue):
        """A new video file needs a new thumbnail and new HLS files."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        video.video_file = make_upload('other.mp4')
        with self.captureOnCommitCallbacks(execute=True):
            video.save()
        self.assertEqual(enqueue.call_args_list, self.expected_calls(video))

    def test_deleting_queues_nothing(self, enqueue):
        """Deleting a video starts no job."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            video.delete()
        enqueue.assert_not_called()

    def test_each_new_video_gets_its_own_jobs(self, enqueue):
        """Two uploads queue two jobs each."""
        first = self.create_video_after_commit()
        second = self.create_video_after_commit(title='Second')
        expected = self.expected_calls(first) + self.expected_calls(second)
        self.assertEqual(enqueue.call_args_list, expected)
