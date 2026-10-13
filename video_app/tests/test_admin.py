import shutil

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from video_app.admin import VideoAdmin
from video_app.models import Video
from video_app.tests.helpers import TEMP_MEDIA_ROOT, create_video, make_upload

CHANGELIST_URL = reverse('admin:video_app_video_changelist')
ADD_URL = reverse('admin:video_app_video_add')


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class VideoAdminTests(TestCase):
    """Tests for the Video admin used to upload videos."""

    @classmethod
    def tearDownClass(cls):
        """Remove uploaded test files after all tests ran."""
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        """Log in as a superuser for every test."""
        self.superuser = User.objects.create_superuser('admin', 'admin@example.com', 'adminpassword')
        self.client.force_login(self.superuser)

    def test_video_is_registered_in_admin(self):
        """The Video model is registered with VideoAdmin."""
        self.assertIsInstance(admin.site._registry[Video], VideoAdmin)

    def test_changelist_is_reachable(self):
        """The video list page loads for a superuser."""
        self.assertEqual(self.client.get(CHANGELIST_URL).status_code, 200)

    def test_add_page_is_reachable(self):
        """The upload form loads for a superuser."""
        self.assertEqual(self.client.get(ADD_URL).status_code, 200)

    def test_changelist_shows_title_and_category(self):
        """Each video row shows its title and category."""
        create_video(title='Matrix', category='Sci-Fi')
        response = self.client.get(CHANGELIST_URL)
        self.assertContains(response, 'Matrix')
        self.assertContains(response, 'Sci-Fi')

    def test_created_at_is_read_only(self):
        """created_at cannot be edited manually."""
        self.assertIn('created_at', VideoAdmin.readonly_fields)

    def test_search_finds_video_by_title(self):
        """The admin search filters videos by title."""
        create_video(title='Matrix')
        create_video(title='Titanic')
        response = self.client.get(CHANGELIST_URL, {'q': 'Matrix'})
        self.assertContains(response, 'Matrix')
        self.assertNotContains(response, 'Titanic')

    def test_filter_shows_only_selected_category(self):
        """The category filter shows only matching videos."""
        create_video(title='Matrix', category='Sci-Fi')
        create_video(title='Titanic', category='Romance')
        response = self.client.get(CHANGELIST_URL, {'category': 'Sci-Fi'})
        self.assertContains(response, 'Matrix')
        self.assertNotContains(response, 'Titanic')


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class VideoAdminUploadTests(TestCase):
    """Uploading videos through the admin form."""

    def setUp(self):
        """Log in as a superuser for every test."""
        self.superuser = User.objects.create_superuser('admin', 'admin@example.com', 'adminpassword')
        self.client.force_login(self.superuser)

    def post_video(self, **overrides):
        """Submit the admin add form with valid default data."""
        data = {
            'title': 'Uploaded Movie',
            'description': 'Uploaded via admin',
            'category': 'Drama',
            'video_file': make_upload(),
        }
        data.update(overrides)
        return self.client.post(ADD_URL, data)

    def test_upload_valid_video_creates_it(self):
        """A complete form saves the video and redirects."""
        response = self.post_video()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Video.objects.filter(title='Uploaded Movie').exists())

    def test_upload_without_required_fields_saves_nothing(self):
        """Missing required fields show the form again and save nothing."""
        response = self.post_video(title='', video_file='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Video.objects.count(), 0)

    def test_upload_non_video_file_is_rejected(self):
        """A PDF is rejected and no video is saved."""
        response = self.post_video(video_file=make_upload('document.pdf', 'application/pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Video.objects.count(), 0)


class VideoAdminAccessTests(TestCase):
    """Only staff users may access the video admin."""

    def test_anonymous_user_is_redirected_to_login(self):
        """Admin pages are not accessible without login."""
        response = self.client.get(CHANGELIST_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response.url)

    def test_normal_user_is_redirected_to_login(self):
        """A logged-in user without staff rights cannot open the admin."""
        user = User.objects.create_user('user@example.com', 'user@example.com', 'password123')
        self.client.force_login(user)
        response = self.client.get(CHANGELIST_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response.url)
