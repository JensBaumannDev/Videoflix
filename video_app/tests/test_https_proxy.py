from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from video_app.models import Video
from video_app.tests.helpers import TempMediaMixin, create_video

PROXY_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


class HttpsProxyTests(TempMediaMixin, APITestCase):
    """Behind nginx with HTTPS the thumbnail URLs use https://."""

    def setUp(self):
        """Log in and create a video with thumbnail."""
        super().setUp()
        user = User.objects.create_user('user@example.com', 'user@example.com', 'pw')
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))
        video = create_video()
        Video.objects.filter(pk=video.pk).update(thumbnail='thumbnail/movie_1a2b3c4d.jpg')

    def thumbnail_url(self, **headers):
        """Return the thumbnail URL from the video list."""
        return self.client.get('/api/video/', **headers).json()[0]['thumbnail_url']

    @override_settings(SECURE_PROXY_SSL_HEADER=PROXY_HEADER)
    def test_forwarded_https_gives_https_urls(self):
        """nginx terminates HTTPS and tells Django via X-Forwarded-Proto."""
        url = self.thumbnail_url(HTTP_X_FORWARDED_PROTO='https')
        self.assertTrue(url.startswith('https://'))

    @override_settings(SECURE_PROXY_SSL_HEADER=PROXY_HEADER)
    def test_forwarded_http_stays_http(self):
        """A plain http request behind the proxy keeps http."""
        url = self.thumbnail_url(HTTP_X_FORWARDED_PROTO='http')
        self.assertTrue(url.startswith('http://'))

    def test_header_is_ignored_without_proxy_setting(self):
        """Locally a client cannot fake https with the header."""
        self.assertIsNone(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))
        url = self.thumbnail_url(HTTP_X_FORWARDED_PROTO='https')
        self.assertTrue(url.startswith('http://'))
