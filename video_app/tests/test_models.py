import shutil
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from video_app.models import Video
from video_app.tests.helpers import TEMP_MEDIA_ROOT, create_video, make_upload


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class VideoModelTests(TestCase):
    """Tests for the Video model."""

    @classmethod
    def tearDownClass(cls):
        """Remove uploaded test files after all tests ran."""
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def test_create_video_saves_all_fields(self):
        """A video stores title, description and category."""
        video = create_video()
        self.assertEqual(video.title, 'Test Movie')
        self.assertEqual(video.description, 'A test description')
        self.assertEqual(video.category, 'Drama')

    def test_str_returns_title(self):
        """The string representation is the title."""
        self.assertEqual(str(create_video(title='Matrix')), 'Matrix')

    def test_created_at_is_set_automatically(self):
        """created_at is filled on creation with the current time."""
        before = timezone.now()
        video = create_video()
        self.assertGreaterEqual(video.created_at, before)
        self.assertLessEqual(video.created_at, timezone.now())

    def test_created_at_does_not_change_on_update(self):
        """Editing a video keeps its original creation date."""
        video = create_video()
        original_created_at = video.created_at
        video.title = 'Corrected Title'
        video.save()
        video.refresh_from_db()
        self.assertEqual(video.created_at, original_created_at)

    def test_video_file_is_uploaded_to_videos_folder(self):
        """Uploaded videos land in media/videos/."""
        video = create_video()
        self.assertTrue(video.video_file.name.startswith('videos/'))

    def test_thumbnail_is_optional(self):
        """A video can be saved without a thumbnail (FFmpeg adds it later)."""
        video = create_video()
        self.assertFalse(video.thumbnail)
        video.full_clean()

    def test_thumbnail_is_uploaded_to_thumbnail_folder(self):
        """Thumbnails land in media/thumbnail/."""
        thumbnail = make_upload('thumb.jpg', 'image/jpeg')
        video = create_video(thumbnail=thumbnail)
        self.assertTrue(video.thumbnail.name.startswith('thumbnail/'))

    def test_videos_are_ordered_newest_first(self):
        """Default ordering is by created_at descending."""
        older = create_video(title='Older')
        newer = create_video(title='Newer')
        Video.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(list(Video.objects.all()), [newer, older])


class VideoModelValidationTests(TestCase):
    """Validation rules of the Video model."""

    def build_video(self, **overrides):
        """Build an unsaved video with valid default values."""
        data = {
            'title': 'Test Movie',
            'description': 'A test description',
            'category': 'Drama',
            'video_file': 'videos/movie.mp4',
        }
        data.update(overrides)
        return Video(**data)

    def test_valid_video_passes_validation(self):
        """A complete video raises no validation error."""
        self.build_video().full_clean()

    def test_missing_required_fields_fail(self):
        """Title, description, category and video file are required."""
        for field in ['title', 'description', 'category', 'video_file']:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    self.build_video(**{field: ''}).full_clean()

    def test_title_longer_than_255_chars_fails(self):
        """Titles are limited to 255 characters."""
        with self.assertRaises(ValidationError):
            self.build_video(title='a' * 256).full_clean()

    def test_title_with_255_chars_is_valid(self):
        """Exactly 255 characters are still allowed."""
        self.build_video(title='a' * 255).full_clean()

    def test_category_longer_than_100_chars_fails(self):
        """Categories are limited to 100 characters."""
        with self.assertRaises(ValidationError):
            self.build_video(category='a' * 101).full_clean()

    def test_category_with_100_chars_is_valid(self):
        """Exactly 100 characters are still allowed."""
        self.build_video(category='a' * 100).full_clean()


class VideoFileFormatTests(TestCase):
    """Only video files may be uploaded, so FFmpeg can convert them."""

    def validate_file(self, file_name):
        """Run model validation for a video with the given file name."""
        video = Video(title='T', description='D', category='C', video_file=f'videos/{file_name}')
        video.full_clean()

    def test_video_formats_are_accepted(self):
        """Common video formats pass validation."""
        for file_name in ['a.mp4', 'a.mov', 'a.mkv', 'a.webm', 'a.avi']:
            with self.subTest(file_name=file_name):
                self.validate_file(file_name)

    def test_uppercase_extension_is_accepted(self):
        """File extensions are checked case-insensitively."""
        self.validate_file('MOVIE.MP4')

    def test_non_video_formats_are_rejected(self):
        """Documents, images and audio files are rejected."""
        for file_name in ['a.pdf', 'a.jpg', 'a.txt', 'a.mp3', 'a.exe']:
            with self.subTest(file_name=file_name):
                with self.assertRaises(ValidationError):
                    self.validate_file(file_name)

    def test_file_without_extension_is_rejected(self):
        """A file without any extension is rejected."""
        with self.assertRaises(ValidationError):
            self.validate_file('movie')

    def test_fake_extension_in_name_is_rejected(self):
        """Only the last extension counts, so movie.mp4.exe is rejected."""
        with self.assertRaises(ValidationError):
            self.validate_file('movie.mp4.exe')
