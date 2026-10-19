import subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from video_app.models import Video
from video_app.tasks import create_thumbnail
from video_app.tests.helpers import (
    FFMPEG_AVAILABLE,
    TempMediaMixin,
    create_video,
    generate_video_upload,
    make_upload,
)
from video_app.utils import (
    THUMBNAIL_POSITION,
    FfmpegError,
    build_thumbnail_name,
    probe_duration,
)

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

    def setUp(self):
        """Pretend every video is 100 seconds long (ffprobe is not called)."""
        super().setUp()
        probe_patcher = patch('video_app.utils.probe_duration', return_value=100.0)
        self.probe_duration = probe_patcher.start()
        self.addCleanup(probe_patcher.stop)

    def fake_ffmpeg_output(self, run):
        """Let the mocked FFmpeg write a dummy JPEG to the output path."""
        def write_output(command, **kwargs):
            """Write fake JPEG bytes to the output path (last argument)."""
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

    def test_seeks_to_ten_percent_of_the_duration(self, run):
        """At 10 % of a 100 s video the frame search starts at 10 s."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-ss') + 1], '10.000')

    def test_seek_comes_before_input_for_fast_seeking(self, run):
        """-ss before -i jumps directly instead of decoding the intro."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        command = run.call_args.args[0]
        self.assertLess(command.index('-ss'), command.index('-i'))

    def test_unknown_duration_starts_at_the_beginning(self, run):
        """If the duration cannot be read, the search starts at 0 s."""
        self.probe_duration.return_value = 0.0
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-ss') + 1], '0.000')

    def test_duration_is_probed_from_the_source_file(self, run):
        """The video file itself is measured."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        create_thumbnail(video.pk)
        self.probe_duration.assert_called_once_with(video.video_file.path)

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
        run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg', stderr='broken')
        video = create_video()
        with self.assertRaises(FfmpegError):
            create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertFalse(video.thumbnail)

    def test_ffmpeg_error_message_reaches_the_job_log(self, run):
        """The FFmpeg error output becomes the exception message."""
        stderr = 'movie.mp4: Invalid data found when processing input'
        run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg', stderr=stderr)
        with self.assertRaisesRegex(FfmpegError, 'Invalid data found'):
            create_thumbnail(create_video().pk)

    def test_ffmpeg_error_without_output(self, run):
        """A failure without stderr still raises a readable error."""
        run.side_effect = subprocess.CalledProcessError(1, 'ffmpeg')
        with self.assertRaisesRegex(FfmpegError, 'exit code 1'):
            create_thumbnail(create_video().pk)

    def test_ffmpeg_output_is_read_as_text(self, run):
        """stderr is decoded so the message is readable, not bytes."""
        self.fake_ffmpeg_output(run)
        create_thumbnail(create_video().pk)
        self.assertTrue(run.call_args.kwargs['text'])

    def test_running_twice_keeps_one_thumbnail(self, run):
        """A second run overwrites the same file instead of adding one."""
        self.fake_ffmpeg_output(run)
        video = create_video()
        create_thumbnail(video.pk)
        create_thumbnail(video.pk)
        files = list((Path(self.media_root) / 'thumbnail').iterdir())
        self.assertEqual([file.name for file in files], ['movie.jpg'])


def completed(stdout, returncode=0):
    """Return a fake finished ffprobe process with the given output."""
    return subprocess.CompletedProcess(['ffprobe'], returncode, stdout=stdout, stderr='')


@patch('video_app.utils.subprocess.run')
class ProbeDurationTests(TestCase):
    """probe_duration reads the video length in seconds with ffprobe."""

    def test_returns_duration_as_float(self, run):
        """ffprobe's output '734.125' becomes 734.125."""
        run.return_value = completed('734.125\n')
        self.assertEqual(probe_duration('/media/videos/movie.mp4'), 734.125)

    def test_asks_ffprobe_for_the_container_duration(self, run):
        """ffprobe reads format=duration of the given file."""
        run.return_value = completed('1.0\n')
        probe_duration('/media/videos/movie.mp4')
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'ffprobe')
        self.assertIn('format=duration', command)
        self.assertEqual(command[-1], '/media/videos/movie.mp4')

    def test_unknown_duration_returns_zero(self, run):
        """'N/A', empty output or garbage fall back to 0.0."""
        for stdout in ['N/A\n', '', 'abc']:
            with self.subTest(stdout=stdout):
                run.return_value = completed(stdout)
                self.assertEqual(probe_duration('/media/videos/movie.mp4'), 0.0)

    def test_ffprobe_error_returns_zero(self, run):
        """A broken file lets ffprobe fail; FFmpeg reports the real error later."""
        run.return_value = completed('', returncode=1)
        self.assertEqual(probe_duration('/media/videos/broken.mp4'), 0.0)

    def test_negative_duration_returns_zero(self, run):
        """Nonsense values never lead to a negative seek position."""
        run.return_value = completed('-5.0\n')
        self.assertEqual(probe_duration('/media/videos/movie.mp4'), 0.0)

    def test_position_is_ten_percent(self, run):
        """The thumbnail position is 10 % of the running time."""
        self.assertEqual(THUMBNAIL_POSITION, 0.1)


def generate_video_with_black_intro(folder):
    """Render 2 s black intro + 18 s test pattern and return it as upload."""
    output = Path(folder) / 'source_intro.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-loglevel', 'error',
         '-f', 'lavfi', '-i', 'color=black:size=320x240:duration=2:rate=10',
         '-f', 'lavfi', '-i', 'testsrc=size=320x240:duration=18:rate=10',
         '-filter_complex', '[0][1]concat=n=2:v=1', '-pix_fmt', 'yuv420p', str(output)],
        check=True)
    return SimpleUploadedFile('intro.mp4', output.read_bytes(), content_type='video/mp4')


def average_brightness(image_path):
    """Return the average luma (about 16 = black) of an image via ffprobe."""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-f', 'lavfi', '-i', f'movie={image_path.name},signalstats',
         '-show_entries', 'frame_tags=lavfi.signalstats.YAVG', '-of', 'csv=p=0'],
        check=True, capture_output=True, text=True, cwd=image_path.parent)
    return float(result.stdout.split()[0])


@skipUnless(FFMPEG_AVAILABLE, 'FFmpeg is not installed')
class CreateThumbnailWithRealFfmpegTests(TempMediaMixin, TestCase):
    """End-to-end tests with a tiny video generated by FFmpeg itself."""

    def test_creates_real_jpeg(self):
        """The thumbnail is a real JPEG file."""
        video = create_video(video_file=generate_video_upload(self.media_root))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(Path(video.thumbnail.path).read_bytes().startswith(b'\xff\xd8'))

    def test_very_short_video(self):
        """A video shorter than one second still gets a thumbnail."""
        video = create_video(video_file=generate_video_upload(self.media_root, duration='0.2'))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertTrue(Path(video.thumbnail.path).is_file())

    def test_other_container_format(self):
        """Other allowed formats like .mkv work as well."""
        video = create_video(video_file=generate_video_upload(self.media_root, 'real.mkv'))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertEqual(video.thumbnail.name, 'thumbnail/real.jpg')

    def test_probe_duration_of_real_video(self):
        """ffprobe measures a generated 5 second video."""
        video = create_video(video_file=generate_video_upload(self.media_root, duration='5'))
        self.assertAlmostEqual(probe_duration(video.video_file.path), 5.0, delta=0.2)

    def test_black_intro_is_skipped(self):
        """With a black intro the thumbnail shows the film, not black."""
        video = create_video(video_file=generate_video_with_black_intro(self.media_root))
        create_thumbnail(video.pk)
        video.refresh_from_db()
        self.assertGreater(average_brightness(Path(video.thumbnail.path)), 40)

    def test_corrupt_video_fails_the_job(self):
        """A file that is no video makes the job fail, no thumbnail is set."""
        video = create_video(video_file=make_upload('broken.mp4'))
        with self.assertRaises(FfmpegError) as context:
            create_thumbnail(video.pk)
        self.assertIn('broken.mp4', str(context.exception))
        video.refresh_from_db()
        self.assertFalse(video.thumbnail)
