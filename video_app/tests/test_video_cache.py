from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from video_app.models import Video
from video_app.tasks import create_thumbnail
from video_app.tests.helpers import TempMediaMixin, create_video
from video_app.utils import (
    VIDEO_LIST_CACHE_KEY,
    VIDEO_LIST_CACHE_TIMEOUT,
    clear_video_list_cache,
    get_video_list,
)


class VideoCacheTestMixin(TempMediaMixin):
    """A logged-in user and helpers to read the video list."""

    def setUp(self):
        """Log in a user via access token cookie."""
        super().setUp()
        self.user = User.objects.create_user('user@example.com', 'user@example.com', 'pw')
        self.client.cookies['access_token'] = str(AccessToken.for_user(self.user))

    def titles(self, **kwargs):
        """Return the titles currently delivered by GET /api/video/."""
        return [item['title'] for item in self.client.get('/api/video/', **kwargs).json()]

    def save_after_commit(self, video):
        """Save a video and run the on_commit callbacks like a real request."""
        with self.captureOnCommitCallbacks(execute=True):
            video.save()


class VideoListCacheTests(VideoCacheTestMixin, APITestCase):
    """The video list is read from the Redis cache after the first request."""

    def test_first_request_fills_the_cache(self):
        """After one request the list is stored under its cache key."""
        create_video(title='Cached')
        self.titles()
        self.assertEqual([video.title for video in cache.get(VIDEO_LIST_CACHE_KEY)], ['Cached'])

    def test_second_request_does_not_query_the_videos(self):
        """With a warm cache only the user is loaded for the JWT check."""
        create_video()
        self.titles()
        with self.assertNumQueries(1):
            self.titles()

    def test_cached_list_is_served(self):
        """Changes without signals (queryset.update) stay invisible until cleared."""
        video = create_video(title='Old title')
        self.titles()
        Video.objects.filter(pk=video.pk).update(title='New title')
        self.assertEqual(self.titles(), ['Old title'])

    def test_empty_list_is_cached_too(self):
        """An empty result is cached and does not hit the database again."""
        self.titles()
        with self.assertNumQueries(1):
            self.assertEqual(self.titles(), [])

    def test_cache_entry_expires(self):
        """A timeout protects against stale data if a clear was ever missed."""
        self.assertGreater(VIDEO_LIST_CACHE_TIMEOUT, 0)
        with patch('video_app.utils.cache') as mocked_cache:
            mocked_cache.get.return_value = None
            get_video_list()
        mocked_cache.set.assert_called_once()
        self.assertEqual(mocked_cache.set.call_args.args[2], VIDEO_LIST_CACHE_TIMEOUT)

    def test_cached_list_keeps_the_order(self):
        """Newest first also for cached results."""
        create_video(title='First')
        create_video(title='Second')
        self.titles()
        self.assertEqual(self.titles(), ['Second', 'First'])


class VideoListCacheSecurityTests(VideoCacheTestMixin, APITestCase):
    """The cache never bypasses authentication or mixes up hosts."""

    def test_warm_cache_still_requires_login(self):
        """Without cookie the cached list is not delivered."""
        create_video()
        self.titles()
        self.client.cookies.clear()
        response = self.client.get('/api/video/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1'])
    def test_thumbnail_url_is_built_per_request_host(self):
        """The absolute URL uses the host of each request, not a cached one."""
        video = create_video()
        Video.objects.filter(pk=video.pk).update(thumbnail='thumbnail/movie_1a2b3c4d.jpg')
        self.client.get('/api/video/')
        response = self.client.get('/api/video/', HTTP_HOST='127.0.0.1')
        url = response.json()[0]['thumbnail_url']
        self.assertEqual(url, 'http://127.0.0.1/media/thumbnail/movie_1a2b3c4d.jpg')


class VideoListCacheInvalidationTests(VideoCacheTestMixin, APITestCase):
    """Every change of a video clears the cached list after the commit."""

    def warm_cache_with(self, **overrides):
        """Create a video and fill the cache with a list request."""
        video = create_video(**overrides)
        self.titles()
        return video

    def test_new_video_clears_the_cache(self):
        """An upload appears in the list right away."""
        self.warm_cache_with(title='Old')
        with self.captureOnCommitCallbacks(execute=True):
            create_video(title='New')
        self.assertEqual(self.titles(), ['New', 'Old'])

    def test_changed_title_clears_the_cache(self):
        """Editing a video in the admin is visible immediately."""
        video = self.warm_cache_with(title='Old title')
        video.title = 'New title'
        self.save_after_commit(video)
        self.assertEqual(self.titles(), ['New title'])

    def test_deleted_video_clears_the_cache(self):
        """A deleted video disappears from the list."""
        video = self.warm_cache_with()
        with self.captureOnCommitCallbacks(execute=True):
            video.delete()
        self.assertEqual(self.titles(), [])

    def test_cache_is_kept_until_commit(self):
        """Without commit (e.g. rollback) the cache is not cleared."""
        video = self.warm_cache_with(title='Old title')
        video.title = 'New title'
        with self.captureOnCommitCallbacks(execute=False):
            video.save()
        self.assertIsNotNone(cache.get(VIDEO_LIST_CACHE_KEY))

    @patch('video_app.utils.probe_duration', return_value=10.0)
    @patch('video_app.utils.run_ffmpeg')
    def test_new_thumbnail_clears_the_cache(self, run_ffmpeg, probe):
        """The thumbnail job updates via queryset, so it clears the cache itself."""
        run_ffmpeg.side_effect = lambda arguments: Path(arguments[-1]).write_bytes(b'jpg')
        video = self.warm_cache_with()
        create_thumbnail(video.pk)
        thumbnail_url = self.client.get('/api/video/').json()[0]['thumbnail_url']
        self.assertIsNotNone(thumbnail_url)

    def test_clear_function_removes_the_entry(self):
        """clear_video_list_cache deletes the cache key."""
        self.warm_cache_with()
        clear_video_list_cache()
        self.assertIsNone(cache.get(VIDEO_LIST_CACHE_KEY))
