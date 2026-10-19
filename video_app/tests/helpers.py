import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from video_app.models import Video

TEMP_MEDIA_ROOT = tempfile.mkdtemp()
FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None


def make_upload(name='movie.mp4', content_type='video/mp4'):
    """Return a fake uploaded file with the given name."""
    return SimpleUploadedFile(name, b'fake-content', content_type=content_type)


def create_video(**overrides):
    """Create and save a video with sensible default values."""
    data = {
        'title': 'Test Movie',
        'description': 'A test description',
        'category': 'Drama',
        'video_file': make_upload(),
    }
    data.update(overrides)
    return Video.objects.create(**data)


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


def build_test_video_command(output, duration='1', size='320x240', audio=False,
                             pix_fmt='yuv420p'):
    """Return the FFmpeg command that renders a test pattern video."""
    command = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
               f'testsrc=duration={duration}:size={size}:rate=10']
    if audio:
        command += ['-f', 'lavfi', '-i', f'sine=duration={duration}']
    return command + ['-pix_fmt', pix_fmt, str(output)]


def generate_video_upload(folder, name='real.mp4', **options):
    """Render a small real video with FFmpeg and return it as upload."""
    output = Path(folder) / f'source_{name}'
    subprocess.run(build_test_video_command(output, **options), check=True)
    return SimpleUploadedFile(name, output.read_bytes(), content_type='video/mp4')
