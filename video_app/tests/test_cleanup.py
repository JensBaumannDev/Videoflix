from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from video_app.models import Video
from video_app.tasks import create_thumbnail
from video_app.tests.helpers import TempMediaMixin, create_video, make_upload
from video_app.utils import HLS_RESOLUTIONS, build_hls_dir, build_hls_root


class CleanupTestMixin(TempMediaMixin):
    """Create videos with thumbnail and HLS files on disk."""

    def create_video_with_files(self, name='movie.mp4'):
        """Create a video plus thumbnail and HLS files like the jobs do."""
        video = create_video(video_file=make_upload(name))
        thumbnail_name = f'thumbnail/{Path(name).stem}.jpg'
        self.write_media_file(thumbnail_name)
        Video.objects.filter(pk=video.pk).update(thumbnail=thumbnail_name)
        for resolution in HLS_RESOLUTIONS:
            folder = build_hls_dir(video.pk, resolution)
            folder.mkdir(parents=True)
            (folder / '000.ts').write_bytes(b'segment')
        video.refresh_from_db()
        return video

    def write_media_file(self, storage_name):
        """Write a dummy file into MEDIA_ROOT."""
        path = Path(self.media_root) / storage_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'data')

    def media_exists(self, storage_name):
        """Return whether a file exists in MEDIA_ROOT."""
        return (Path(self.media_root) / storage_name).exists()

    def delete_after_commit(self, video):
        """Delete the video and run the on_commit callbacks."""
        with self.captureOnCommitCallbacks(execute=True):
            video.delete()


class DeleteVideoCleanupTests(CleanupTestMixin, TestCase):
    """Deleting a video removes all of its files."""

    def test_removes_video_file(self):
        """The uploaded original is deleted."""
        video = self.create_video_with_files()
        video_name = video.video_file.name
        self.delete_after_commit(video)
        self.assertFalse(self.media_exists(video_name))

    def test_removes_thumbnail(self):
        """The thumbnail image is deleted."""
        video = self.create_video_with_files()
        thumbnail_name = video.thumbnail.name
        self.delete_after_commit(video)
        self.assertFalse(self.media_exists(thumbnail_name))

    def test_removes_whole_hls_folder(self):
        """hls/<id>/ with all resolutions is deleted."""
        video = self.create_video_with_files()
        hls_root = build_hls_root(video.pk)
        self.delete_after_commit(video)
        self.assertFalse(hls_root.exists())

    def test_files_stay_until_commit(self):
        """Without commit (e.g. a rollback) no file is touched."""
        video = self.create_video_with_files()
        video_id, video_name = video.pk, video.video_file.name
        with self.captureOnCommitCallbacks(execute=False):
            video.delete()
        self.assertTrue(self.media_exists(video_name))
        self.assertTrue(build_hls_root(video_id).exists())

    def test_other_videos_keep_their_files(self):
        """Only the deleted video's files disappear."""
        other = self.create_video_with_files('other.mp4')
        video = self.create_video_with_files('movie.mp4')
        self.delete_after_commit(video)
        self.assertTrue(self.media_exists(other.video_file.name))
        self.assertTrue(self.media_exists(other.thumbnail.name))
        self.assertTrue(build_hls_root(other.pk).exists())

    def test_video_without_thumbnail_and_hls(self):
        """A video whose jobs never ran is deleted without error."""
        video = create_video()
        video_name = video.video_file.name
        self.delete_after_commit(video)
        self.assertFalse(self.media_exists(video_name))

    def test_files_already_missing_on_disk(self):
        """Missing files do not make the deletion fail."""
        video = self.create_video_with_files()
        Path(video.video_file.path).unlink()
        Path(video.thumbnail.path).unlink()
        self.delete_after_commit(video)
        self.assertFalse(Video.objects.exists())

    def test_bulk_delete_removes_all_files(self):
        """Deleting several videos at once (admin action) cleans up each."""
        first = self.create_video_with_files('first.mp4')
        second = self.create_video_with_files('second.mp4')
        names = [first.video_file.name, second.video_file.name]
        with self.captureOnCommitCallbacks(execute=True):
            Video.objects.all().delete()
        self.assertFalse(any(self.media_exists(name) for name in names))

    def test_delete_queues_no_jobs(self):
        """Cleanup runs directly, no RQ job is started."""
        video = self.create_video_with_files()
        with patch('django_rq.enqueue') as enqueue:
            self.delete_after_commit(video)
        enqueue.assert_not_called()


class ReplaceVideoFileCleanupTests(CleanupTestMixin, TestCase):
    """Replacing the video file removes the old original."""

    def replace_file(self, video, name='new.mp4'):
        """Upload a new file for the video and run the commit callbacks."""
        video.video_file = make_upload(name)
        with self.captureOnCommitCallbacks(execute=True):
            video.save()

    def test_old_video_file_is_deleted(self):
        """The previous original disappears after the commit."""
        video = self.create_video_with_files()
        old_name = video.video_file.name
        self.replace_file(video)
        self.assertFalse(self.media_exists(old_name))

    def test_new_video_file_is_kept(self):
        """The new upload stays on disk."""
        video = self.create_video_with_files()
        self.replace_file(video)
        self.assertTrue(self.media_exists(video.video_file.name))

    def test_old_file_stays_until_commit(self):
        """Without commit the old original is not deleted."""
        video = self.create_video_with_files()
        old_name = video.video_file.name
        video.video_file = make_upload('new.mp4')
        with self.captureOnCommitCallbacks(execute=False):
            video.save()
        self.assertTrue(self.media_exists(old_name))

    def test_saving_without_new_file_deletes_nothing(self):
        """Editing the title keeps the video file."""
        video = self.create_video_with_files()
        video.title = 'New title'
        with self.captureOnCommitCallbacks(execute=True):
            video.save()
        self.assertTrue(self.media_exists(video.video_file.name))

    def test_upload_with_same_name_keeps_the_new_file(self):
        """Django renames a second movie.mp4, so the new file survives."""
        video = self.create_video_with_files('movie.mp4')
        self.replace_file(video, 'movie.mp4')
        self.assertTrue(self.media_exists(video.video_file.name))

    def test_new_video_deletes_nothing(self):
        """Creating a video never deletes files of other videos."""
        other = self.create_video_with_files('other.mp4')
        with self.captureOnCommitCallbacks(execute=True):
            create_video(video_file=make_upload('movie.mp4'))
        self.assertTrue(self.media_exists(other.video_file.name))


@patch('video_app.utils.probe_duration', return_value=100.0)
@patch('video_app.utils.run_ffmpeg')
class ThumbnailReplacementTests(CleanupTestMixin, TestCase):
    """A thumbnail with a new name replaces the old image file."""

    def fake_thumbnail(self, run_ffmpeg):
        """Let the mocked FFmpeg write the output file (last argument)."""
        run_ffmpeg.side_effect = lambda arguments: Path(arguments[-1]).write_bytes(b'jpg')

    def test_old_thumbnail_with_other_name_is_deleted(self, run_ffmpeg, probe):
        """After replacing the video file, the old thumbnail disappears."""
        self.fake_thumbnail(run_ffmpeg)
        video = self.create_video_with_files('movie.mp4')
        old_thumbnail = video.thumbnail.name
        self.write_media_file('videos/new.mp4')
        Video.objects.filter(pk=video.pk).update(video_file='videos/new.mp4')
        create_thumbnail(video.pk)
        self.assertFalse(self.media_exists(old_thumbnail))
        self.assertTrue(self.media_exists('thumbnail/new.jpg'))

    def test_thumbnail_with_same_name_is_kept(self, run_ffmpeg, probe):
        """Regenerating with the same name keeps the new file."""
        self.fake_thumbnail(run_ffmpeg)
        video = self.create_video_with_files('movie.mp4')
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(self.media_exists(video.thumbnail.name))

    def test_first_thumbnail_deletes_nothing(self, run_ffmpeg, probe):
        """A video without thumbnail yet just gets one."""
        self.fake_thumbnail(run_ffmpeg)
        video = create_video()
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(self.media_exists(video.thumbnail.name))
