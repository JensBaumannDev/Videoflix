import subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase

from video_app.models import Video
from video_app.tasks import convert_to_hls
from video_app.tests.helpers import (
    FFMPEG_AVAILABLE,
    TempMediaMixin,
    create_video,
    generate_video_upload,
    make_upload,
)
from video_app.utils import HLS_RESOLUTIONS, build_hls_command, build_hls_dir


class HlsSettingsTests(TestCase):
    """The resolutions match the frontend's quality switch."""

    def test_resolutions_and_heights(self):
        """480p, 720p and 1080p map to their pixel heights."""
        self.assertEqual(HLS_RESOLUTIONS, {'480p': 480, '720p': 720, '1080p': 1080})


class HlsPathTests(TempMediaMixin, TestCase):
    """HLS files live in MEDIA_ROOT/hls/<video id>/<resolution>/."""

    def test_hls_dir_per_video_and_resolution(self):
        """The folder contains video id and resolution."""
        video = create_video()
        expected = Path(self.media_root) / 'hls' / str(video.pk) / '720p'
        self.assertEqual(build_hls_dir(video.pk, '720p'), expected)

    def test_videos_do_not_share_folders(self):
        """Two videos get different folders."""
        first, second = create_video(), create_video()
        self.assertNotEqual(build_hls_dir(first.pk, '480p'), build_hls_dir(second.pk, '480p'))


class HlsCommandTests(TestCase):
    """The FFmpeg arguments produce a VOD playlist with 000.ts segments."""

    def setUp(self):
        """Build the arguments for a 480p conversion."""
        self.output_dir = Path('/media/hls/1/480p')
        self.command = build_hls_command('/media/videos/movie.mp4', self.output_dir, 480)

    def value_of(self, option):
        """Return the value that follows an option in the arguments."""
        return self.command[self.command.index(option) + 1]

    def test_reads_the_source_file(self):
        """The source video is the input."""
        self.assertEqual(self.value_of('-i'), '/media/videos/movie.mp4')

    def test_scales_to_height_with_even_width(self):
        """-2 keeps the aspect ratio with an even width for H.264."""
        self.assertEqual(self.value_of('-vf'), 'scale=-2:480')

    def test_uses_browser_compatible_codecs(self):
        """H.264 video in yuv420p and AAC audio play in every browser."""
        self.assertEqual(self.value_of('-c:v'), 'libx264')
        self.assertEqual(self.value_of('-pix_fmt'), 'yuv420p')
        self.assertEqual(self.value_of('-c:a'), 'aac')

    def test_maps_first_video_and_optional_audio(self):
        """Subtitles or extra tracks are ignored, missing audio is fine."""
        maps = [self.command[i + 1] for i, arg in enumerate(self.command) if arg == '-map']
        self.assertEqual(maps, ['0:v:0', '0:a:0?'])

    def test_writes_vod_hls_playlist(self):
        """Output format is HLS with a complete VOD playlist."""
        self.assertEqual(self.value_of('-f'), 'hls')
        self.assertEqual(self.value_of('-hls_playlist_type'), 'vod')
        self.assertEqual(self.value_of('-hls_time'), '10')

    def test_segment_names_match_endpoint_format(self):
        """Segments are named 000.ts, 001.ts ... next to the playlist."""
        expected = str(self.output_dir / '%03d.ts')
        self.assertEqual(self.value_of('-hls_segment_filename'), expected)

    def test_playlist_is_index_m3u8(self):
        """The last argument is the playlist path."""
        self.assertEqual(self.command[-1], str(self.output_dir / 'index.m3u8'))


@patch('video_app.utils.run_ffmpeg')
class ConvertToHlsTaskTests(TempMediaMixin, TestCase):
    """The RQ task converts the video once per resolution."""

    def converted_scales(self, run_ffmpeg):
        """Return the scale filters FFmpeg was called with, in order."""
        return [args[0][args[0].index('-vf') + 1] for args, _ in run_ffmpeg.call_args_list]

    def test_converts_all_three_resolutions(self, run_ffmpeg):
        """FFmpeg runs for 480p, 720p and 1080p."""
        convert_to_hls(create_video().pk)
        self.assertEqual(self.converted_scales(run_ffmpeg),
                         ['scale=-2:480', 'scale=-2:720', 'scale=-2:1080'])

    def test_creates_one_folder_per_resolution(self, run_ffmpeg):
        """Each resolution gets its own folder before FFmpeg runs."""
        video = create_video()
        convert_to_hls(video.pk)
        for resolution in HLS_RESOLUTIONS:
            with self.subTest(resolution=resolution):
                self.assertTrue(build_hls_dir(video.pk, resolution).is_dir())

    def test_removes_old_files_before_converting(self, run_ffmpeg):
        """Segments of a replaced video file do not survive."""
        video = create_video()
        old_segment = build_hls_dir(video.pk, '480p') / '099.ts'
        old_segment.parent.mkdir(parents=True)
        old_segment.write_bytes(b'old')
        convert_to_hls(video.pk)
        self.assertFalse(old_segment.exists())

    def test_other_videos_are_not_touched(self, run_ffmpeg):
        """Only the folder of this video is cleaned."""
        other, video = create_video(), create_video()
        other_segment = build_hls_dir(other.pk, '480p') / '000.ts'
        other_segment.parent.mkdir(parents=True)
        other_segment.write_bytes(b'keep')
        convert_to_hls(video.pk)
        self.assertTrue(other_segment.exists())

    def test_unknown_video_id_does_nothing(self, run_ffmpeg):
        """A deleted or unknown video is skipped without error."""
        convert_to_hls(999999)
        run_ffmpeg.assert_not_called()

    def test_missing_source_file_does_nothing(self, run_ffmpeg):
        """If the video file is gone from disk, FFmpeg is not called."""
        video = create_video()
        Path(video.video_file.path).unlink()
        convert_to_hls(video.pk)
        run_ffmpeg.assert_not_called()

    def test_video_without_file_does_nothing(self, run_ffmpeg):
        """A video row without file name is skipped."""
        video = create_video()
        Video.objects.filter(pk=video.pk).update(video_file='')
        convert_to_hls(video.pk)
        run_ffmpeg.assert_not_called()

    def test_ffmpeg_error_fails_the_job(self, run_ffmpeg):
        """A failing FFmpeg run stops the job and raises."""
        run_ffmpeg.side_effect = subprocess.CalledProcessError(1, 'ffmpeg')
        with self.assertRaises(subprocess.CalledProcessError):
            convert_to_hls(create_video().pk)
        self.assertEqual(run_ffmpeg.call_count, 1)

    def test_does_not_queue_new_jobs(self, run_ffmpeg):
        """The conversion never triggers the save signal."""
        video = create_video()
        with patch('django_rq.enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                convert_to_hls(video.pk)
        enqueue.assert_not_called()


def probe_stream(media_path, stream, entry):
    """Return one stream property via ffprobe (.ts files list streams twice)."""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', stream, '-show_entries',
         f'stream={entry}', '-of', 'csv=p=0', str(media_path)],
        check=True, capture_output=True, text=True)
    return result.stdout.split()[0]


@skipUnless(FFMPEG_AVAILABLE, 'FFmpeg is not installed')
class ConvertToHlsWithRealFfmpegTests(TempMediaMixin, TestCase):
    """End-to-end tests with tiny videos generated by FFmpeg itself."""

    def convert(self, **video_options):
        """Upload a generated video, convert it and return its id."""
        upload = generate_video_upload(self.media_root, **video_options)
        video = create_video(video_file=upload)
        convert_to_hls(video.pk)
        return video.pk

    def test_writes_playlist_and_first_segment_per_resolution(self):
        """Each resolution has index.m3u8 and 000.ts."""
        video_id = self.convert(audio=True)
        for resolution in HLS_RESOLUTIONS:
            with self.subTest(resolution=resolution):
                folder = build_hls_dir(video_id, resolution)
                self.assertTrue((folder / 'index.m3u8').is_file())
                self.assertTrue((folder / '000.ts').is_file())

    def test_playlist_is_complete_vod_with_relative_segments(self):
        """The playlist is valid, ends properly and links 000.ts relatively."""
        playlist = (build_hls_dir(self.convert(), '480p') / 'index.m3u8').read_text()
        self.assertTrue(playlist.startswith('#EXTM3U'))
        self.assertIn('#EXT-X-PLAYLIST-TYPE:VOD', playlist)
        self.assertIn('#EXT-X-ENDLIST', playlist)
        self.assertIn('\n000.ts\n', playlist)

    def test_segments_have_the_target_height(self):
        """480p, 720p and 1080p segments really have that height."""
        video_id = self.convert()
        for resolution, height in HLS_RESOLUTIONS.items():
            with self.subTest(resolution=resolution):
                segment = build_hls_dir(video_id, resolution) / '000.ts'
                self.assertEqual(int(probe_stream(segment, 'v:0', 'height')), height)

    def test_video_with_audio_keeps_audio(self):
        """The audio track ends up in the segments."""
        segment = build_hls_dir(self.convert(audio=True), '480p') / '000.ts'
        self.assertEqual(probe_stream(segment, 'a:0', 'codec_name'), 'aac')

    def test_video_without_audio(self):
        """A silent video converts without error."""
        video_id = self.convert(audio=False)
        self.assertTrue((build_hls_dir(video_id, '1080p') / 'index.m3u8').is_file())

    def test_odd_size_and_other_pixel_format(self):
        """Odd dimensions and 4:4:4 sources end up as browser-safe yuv420p."""
        video_id = self.convert(size='321x241', pix_fmt='yuv444p')
        segment = build_hls_dir(video_id, '720p') / '000.ts'
        self.assertEqual(probe_stream(segment, 'v:0', 'pix_fmt'), 'yuv420p')

    def test_long_video_is_split_into_several_segments(self):
        """A video longer than one segment produces 000.ts and 001.ts."""
        video_id = self.convert(duration='25')
        self.assertTrue((build_hls_dir(video_id, '480p') / '001.ts').is_file())

    def test_corrupt_video_fails_the_job(self):
        """A file that is no video makes the job fail."""
        video = create_video(video_file=make_upload('broken.mp4'))
        with self.assertRaises(subprocess.CalledProcessError):
            convert_to_hls(video.pk)
