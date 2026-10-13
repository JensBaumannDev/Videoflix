import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile

from video_app.models import Video

TEMP_MEDIA_ROOT = tempfile.mkdtemp()


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
