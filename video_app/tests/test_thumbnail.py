import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from video_app.models import Video
from video_app.tasks import create_thumbnail
from video_app.tests.helpers import create_video, make_upload
from video_app.utils import build_thumbnail_name

FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None


class TempMediaMixin:
    """Run each test with its own empty MEDIA_ROOT."""

    def setUp(self):
        """Point MEDIA_ROOT to a fresh temporary folder."""
        self.media_root = tempfile.mkdtemp()
        media_override = override_settings(MEDIA_ROOT=self.media_root)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

    def create_video_after_commit(self, **overrides):
        """Create a video and run the on_commit callbacks like a real request."""
        with self.captureOnCommitCallbacks(execute=True):
            return create_video(**overrides)


@patch('django_rq.enqueue')
class ThumbnailSignalTests(TempMediaMixin, TestCase):
    """Saving a video queues the thumbnail task only when needed."""

    def test_new_video_queues_thumbnail_task(self, enqueue):
        """A new video queues create_thumbnail once with its id."""
        video = self.create_video_after_commit()
        enqueue.assert_called_once_with(create_thumbnail, video.pk)

    def test_task_is_queued_only_after_commit(self, enqueue):
        """The job waits for the commit so the worker finds the row."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            create_video()
        enqueue.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_changing_other_fields_queues_nothing(self, enqueue):
        """Editing title, description or category needs no new thumbnail."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        video.title = 'New title'
        with self.captureOnCommitCallbacks(execute=True):
            video.save()
        enqueue.assert_not_called()

    def test_replacing_the_video_file_queues_again(self, enqueue):
        """A new video file needs a new thumbnail."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        video.video_file = make_upload('other.mp4')
        with self.captureOnCommitCallbacks(execute=True):
            video.save()
        enqueue.assert_called_once_with(create_thumbnail, video.pk)

    def test_deleting_queues_nothing(self, enqueue):
        """Deleting a video starts no thumbnail job."""
        video = self.create_video_after_commit()
        enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            video.delete()
        enqueue.assert_not_called()

    def test_each_new_video_gets_its_own_job(self, enqueue):
        """Two uploads queue two jobs."""
        first = self.create_video_after_commit()
        second = self.create_video_after_commit(title='Second')
        self.assertEqual(enqueue.call_count, 2)
        enqueue.assert_any_call(create_thumbnail, first.pk)
        enqueue.assert_any_call(create_thumbnail, second.pk)


class ThumbnailNameTests(TempMediaMixin, TestCase):
    """The thumbnail file is named after the video file."""

    def test_name_uses_video_file_stem(self):
        """videos/movie.mp4 becomes thumbnail/movie.jpg."""
        video = create_video(video_file=make_upload('movie.mp4'))
        self.assertEqual(build_thumbnail_name(video), 'thumbnail/movie.jpg')

    def test_name_for_other_extensions(self):
        """The original extension is replaced by .jpg."""
        video = create_video(video_file=make_upload('clip.MOV', 'video/quicktime'))
        self.assertEqual(build_thumbnail_name(video), 'thumbnail/clip.jpg')

    def test_name_with_dots_in_file_name(self):
        """Only the last extension is replaced."""
        video = create_video(video_file=make_upload('my.holiday.mp4'))
        self.assertEqual(build_thumbnail_name(video), 'thumbnail/my.holiday.jpg')

    def test_duplicate_upload_names_get_different_thumbnails(self):
        """Django renames the second upload, so thumbnails do not collide."""
        first = create_video(video_file=make_upload('movie.mp4'))
        second = create_video(video_file=make_upload('movie.mp4'))
        self.assertNotEqual(build_thumbnail_name(first), build_thumbnail_name(second))


@patch('video_app.utils.subprocess.run')
class CreateThumbnailTaskTests(TempMediaMixin, TestCase):
    """The RQ task calls FFmpeg and stores the thumbnail path."""

    def fake_ffmpeg_output(self, run):
        """Let the mocked FFmpeg write a dummy JPEG to the output path."""
        def write_output(command, **kwargs):
            Path(command[-1]).write_bytes(b'\xff\xd8fake-jpeg')
        run.side_effect = write_output

    def test_calls_ffmpeg_with_input_and_output(self, run):
        """FFmpeg reads the video file and writes into MEDIA_ROOT/thumbnail."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        create_thumbnail(video.pk)
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'ffmpeg')
        self.assertIn(video.video_file.path, command)
        expected = Path(self.media_root) / 'thumbnail' / 'movie.jpg'
        self.assertEqual(Path(command[-1]), expected)

    def test_ffmpeg_extracts_exactly_one_frame(self, run):
        """Only a single still image is written."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-frames:v') + 1], '1')

    def test_ffmpeg_overwrites_and_fails_loudly(self, run):
        """-y overwrites old thumbnails, check=True raises on errors."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        self.assertIn('-y', run.call_args.args[0])
        self.assertTrue(run.call_args.kwargs['check'])

    def test_saves_thumbnail_path_on_video(self, run):
        """The video points to the new thumbnail afterwards."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertEqual(video.thumbnail.name, 'thumbnail/movie.jpg')
        self.assertTrue(Path(video.thumbnail.path).is_file())

    def test_creates_missing_thumbnail_folder(self, run):
        """The thumbnail folder is created on the first run."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        self.assertTrue((Path(self.media_root) / 'thumbnail').is_dir())

    def test_does_not_queue_itself_again(self, run):
        """Storing the thumbnail does not trigger the signal again."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        with patch('django_rq.enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                create_thumbnail(video.pk)
        enqueue.assert_not_called()

    def test_unknown_video_id_does_nothing(self, run):
        """A deleted or unknown video is skipped without error."""
        create_thumbnail(999999)
        run.assert_not_called()

    def test_missing_source_file_does_nothing(self, run):
        """If the video file is gone from disk, FFmpeg is not called."""
        video = create_video()
        Path(video.video_file.path).unlink()
        create_thumbnail(video.pk)
        run.assert_not_called()

    def test_video_without_file_does_nothing(self, run):
        """A video row without file name is skipped."""
        video = create_video()
        Video.objects.filter(pk=video.pk).update(video_file='')
        create_thumbnail(video.pk)
        run.assert_not_called()

    def test_ffmpeg_error_keeps_thumbnail_empty(self, run):
        """A failing FFmpeg run fails the job and stores nothing."""
        run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg')
        video = create_video()
        with self.assertRaises(subprocess.CalledProcessError):
            create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertFalse(video.thumbnail)

    def test_running_twice_keeps_one_thumbnail(self, run):
        """A second run overwrites the same file instead of adding one."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        create_thumbnail(video.pk)
        create_thumbnail(video.pk)
        files = list((Path(self.media_root) / 'thumbnail').iterdir())
        self.assertEqual([file.name for file in files], ['movie.jpg'])


@skipUnless(FFMPEG_AVAILABLE, 'FFmpeg is not installed')
class CreateThumbnailWithRealFfmpegTests(TempMediaMixin, TestCase):
    """End-to-end tests with a tiny video generated by FFmpeg itself."""

    def make_real_video(self, duration='1', name='real.mp4'):
        """Generate a small test video with FFmpeg and return it as upload."""
        output = Path(self.media_root) / f'source_{name}'
        subprocess.run(
            ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
             f'testsrc=duration={duration}:size=320x240:rate=10', str(output)],
            check=True,
        )
        return SimpleUploadedFile(name, output.read_bytes(), content_type='video/mp4')

    def test_creates_real_jpeg(self):
        """The thumbnail is a real JPEG file."""
        video = create_video(video_file=self.make_real_video())
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(Path(video.thumbnail.path).read_bytes().startswith(b'\xff\xd8'))

    def test_very_short_video(self):
        """A video shorter than one second still gets a thumbnail."""
        video = create_video(video_file=self.make_real_video(duration='0.2'))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(Path(video.thumbnail.path).is_file())

    def test_other_container_format(self):
        """Other allowed formats like .mkv work as well."""
        video = create_video(video_file=self.make_real_video(name='real.mkv'))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertEqual(video.thumbnail.name, 'thumbnail/real.jpg')

    def test_corrupt_video_fails_the_job(self):
        """A file that is no video makes the job fail, no thumbnail is set."""
        video = create_video(video_file=make_upload('broken.mp4'))
        with self.assertRaises(subprocess.CalledProcessError):
            create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertFalse(video.thumbnail)
