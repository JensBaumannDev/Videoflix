from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from video_app.models import Video
from video_app.tasks import convert_to_hls
from video_app.tests.helpers import TempMediaMixin, create_video
from video_app.utils import (
    HLS_RESOLUTIONS,
    FfmpegError,
    build_hls_dir,
    build_hls_root,
    build_temp_dir,
    find_hls_file,
)


def write_fake_rendition(arguments):
    """Act like FFmpeg: write playlist and segment next to the playlist path."""
    playlist = Path(arguments[-1])
    playlist.write_bytes(b'#EXTM3U\n000.ts\n#EXT-X-ENDLIST\n')
    (playlist.parent / '000.ts').write_bytes(b'segment')


class HlsTempDirNameTests(TestCase):
    """Each resolution has a temporary sibling folder."""

    def test_temp_dir_name(self):
        """hls/1/480p is written as hls/1/480p.tmp first."""
        output_dir = Path('/media/hls/1/480p')
        self.assertEqual(build_temp_dir(output_dir), Path('/media/hls/1/480p.tmp'))


@patch('video_app.utils.run_ffmpeg', side_effect=write_fake_rendition)
class HlsTempDirTests(TempMediaMixin, TestCase):
    """FFmpeg writes into <resolution>.tmp, which becomes <resolution> when done."""

    def test_ffmpeg_writes_into_temp_dir(self, run_ffmpeg):
        """The playlist path given to FFmpeg lies in the .tmp folder."""
        video = create_video()
        convert_to_hls(video.pk)
        playlist = Path(run_ffmpeg.call_args_list[0].args[0][-1])
        self.assertEqual(playlist.parent, build_temp_dir(build_hls_dir(video.pk, '480p')))

    def test_finished_files_end_up_in_final_dir(self, run_ffmpeg):
        """After the job every resolution has its playlist in the final folder."""
        video = create_video()
        convert_to_hls(video.pk)
        for resolution in HLS_RESOLUTIONS:
            with self.subTest(resolution=resolution):
                self.assertTrue((build_hls_dir(video.pk, resolution) / '000.ts').is_file())

    def test_no_temp_dirs_are_left(self, run_ffmpeg):
        """All .tmp folders are gone after a successful job."""
        video = create_video()
        convert_to_hls(video.pk)
        leftovers = list(build_hls_root(video.pk).glob('*.tmp'))
        self.assertEqual(leftovers, [])

    def test_final_dir_is_missing_while_converting(self, run_ffmpeg):
        """During the first conversion the endpoint finds no half playlist."""
        video = create_video()
        seen = []

        def check_and_write(arguments):
            """Record whether the playlist is already public, then write files."""
            seen.append(find_hls_file(video.pk, '480p', 'index.m3u8'))
            write_fake_rendition(arguments)
        run_ffmpeg.side_effect = check_and_write
        convert_to_hls(video.pk)
        self.assertIsNone(seen[0])

    def test_old_temp_leftovers_are_removed(self, run_ffmpeg):
        """Files from a crashed earlier run in .tmp do not survive."""
        video = create_video()
        temp_dir = build_temp_dir(build_hls_dir(video.pk, '480p'))
        temp_dir.mkdir(parents=True)
        (temp_dir / '999.ts').write_bytes(b'old')
        convert_to_hls(video.pk)
        self.assertFalse((build_hls_dir(video.pk, '480p') / '999.ts').exists())

    def test_temp_dir_is_never_served(self, run_ffmpeg):
        """'480p.tmp' is no valid resolution for the stream endpoints."""
        video = create_video()
        temp_dir = build_temp_dir(build_hls_dir(video.pk, '480p'))
        temp_dir.mkdir(parents=True)
        (temp_dir / 'index.m3u8').write_bytes(b'#EXTM3U')
        self.assertIsNone(find_hls_file(video.pk, '480p.tmp', 'index.m3u8'))


@patch('video_app.utils.run_ffmpeg')
class HlsFailedConversionTests(TempMediaMixin, TestCase):
    """A failed conversion leaves no half files and keeps a working old version."""

    def write_old_version(self, video_id):
        """Write a complete older 480p version like a previous job did."""
        folder = build_hls_dir(video_id, '480p')
        folder.mkdir(parents=True)
        (folder / 'index.m3u8').write_bytes(b'old playlist')

    def test_temp_dir_is_removed_on_error(self, run_ffmpeg):
        """The half written .tmp folder disappears."""
        run_ffmpeg.side_effect = FfmpegError('broken')
        video = create_video()
        with self.assertRaises(FfmpegError):
            convert_to_hls(video.pk)
        self.assertFalse(build_temp_dir(build_hls_dir(video.pk, '480p')).exists())

    def test_old_version_is_kept_on_error(self, run_ffmpeg):
        """A working older playlist keeps being served."""
        run_ffmpeg.side_effect = FfmpegError('broken')
        video = create_video()
        self.write_old_version(video.pk)
        with self.assertRaises(FfmpegError):
            convert_to_hls(video.pk)
        playlist = build_hls_dir(video.pk, '480p') / 'index.m3u8'
        self.assertEqual(playlist.read_bytes(), b'old playlist')

    def test_finished_resolutions_stay_when_a_later_one_fails(self, run_ffmpeg):
        """If 720p fails, the already finished 480p is still usable."""
        run_ffmpeg.side_effect = [None, FfmpegError('broken')]
        video = create_video()
        with self.assertRaises(FfmpegError):
            convert_to_hls(video.pk)
        self.assertTrue(build_hls_dir(video.pk, '480p').is_dir())
        self.assertFalse(build_hls_dir(video.pk, '720p').exists())
        self.assertEqual(list(build_hls_root(video.pk).glob('*.tmp')), [])

    def test_old_version_is_replaced_on_success(self, run_ffmpeg):
        """A successful run replaces the older playlist."""
        run_ffmpeg.side_effect = write_fake_rendition
        video = create_video()
        self.write_old_version(video.pk)
        convert_to_hls(video.pk)
        playlist = build_hls_dir(video.pk, '480p') / 'index.m3u8'
        self.assertNotEqual(playlist.read_bytes(), b'old playlist')


@patch('video_app.utils.run_ffmpeg', side_effect=write_fake_rendition)
class HlsDeletedDuringConversionTests(TempMediaMixin, TestCase):
    """Videos deleted while converting leave no orphaned HLS files."""

    def test_hls_folder_is_removed_if_video_was_deleted(self, run_ffmpeg):
        """The job removes its own output when the video is gone afterwards."""
        video = create_video()

        def delete_video_then_write(arguments):
            """Simulate the admin deleting the video during the conversion."""
            Video.objects.filter(pk=video.pk).delete()
            write_fake_rendition(arguments)
        run_ffmpeg.side_effect = delete_video_then_write
        convert_to_hls(video.pk)
        self.assertFalse(build_hls_root(video.pk).exists())

    def test_existing_video_keeps_its_folder(self, run_ffmpeg):
        """Without deletion the HLS folder stays."""
        video = create_video()
        convert_to_hls(video.pk)
        self.assertTrue(build_hls_root(video.pk).exists())
