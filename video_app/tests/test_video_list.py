from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from video_app.models import Video
from video_app.tests.helpers import TempMediaMixin, create_video

DOC_FIELDS = {'id', 'created_at', 'title', 'description', 'thumbnail_url', 'category'}


class VideoListTestMixin(TempMediaMixin):
    """Shared helpers for the video list endpoint tests."""

    def setUp(self):
        """Create an active user that is logged in via access token cookie."""
        super().setUp()
        self.user = User.objects.create_user('user@example.com', 'user@example.com', 'pw')
        self.client.cookies['access_token'] = str(AccessToken.for_user(self.user))

    def list_videos(self, **kwargs):
        """Send a GET request to the video list endpoint."""
        return self.client.get(reverse('video-list'), **kwargs)

    def create_video_with_thumbnail(self, thumbnail='thumbnail/movie.jpg', **overrides):
        """Create a video and set its thumbnail path like the task does."""
        video = create_video(**overrides)
        Video.objects.filter(pk=video.pk).update(thumbnail=thumbnail)
        return video

    def set_created_at(self, video, days_ago):
        """Move the creation date of a video into the past."""
        created_at = timezone.now() - timedelta(days=days_ago)
        Video.objects.filter(pk=video.pk).update(created_at=created_at)


class VideoListSuccessTests(VideoListTestMixin, APITestCase):
    """Logged-in users get all videos in the documented format."""

    def test_returns_200_with_list(self):
        """The response is a JSON list."""
        self.create_video_with_thumbnail()
        response = self.list_videos()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.json(), list)

    def test_items_have_exactly_the_doc_fields(self):
        """No field is missing and nothing else (like file paths) leaks."""
        self.create_video_with_thumbnail()
        item = self.list_videos().json()[0]
        self.assertEqual(set(item), DOC_FIELDS)

    def test_field_values(self):
        """id, title, description and category are returned unchanged."""
        video = self.create_video_with_thumbnail(
            title='Big Buck Bunny', description='A rabbit.', category='Comedy')
        item = self.list_videos().json()[0]
        expected = {'id': video.pk, 'title': 'Big Buck Bunny',
                    'description': 'A rabbit.', 'category': 'Comedy'}
        self.assertEqual({key: item[key] for key in expected}, expected)

    def test_category_spelling_is_kept(self):
        """'drama' and 'Drama' come back as stored; the frontend groups them."""
        self.create_video_with_thumbnail(category='drama')
        self.create_video_with_thumbnail(category='Drama')
        categories = {item['category'] for item in self.list_videos().json()}
        self.assertEqual(categories, {'drama', 'Drama'})

    def test_number_of_queries_does_not_grow_with_videos(self):
        """One query for the user and one for all videos, no N+1."""
        for number in range(5):
            self.create_video_with_thumbnail(title=f'Video {number}')
        with self.assertNumQueries(2):
            self.list_videos()

    def test_special_characters_are_kept(self):
        """Umlauts, quotes and symbols survive the serialization."""
        title = 'Tränen & "Stahl" – 100% <Sci-Fi>'
        self.create_video_with_thumbnail(title=title)
        self.assertEqual(self.list_videos().json()[0]['title'], title)

    def test_created_at_is_iso_8601_utc(self):
        """created_at looks like 2023-01-01T12:00:00Z (with optional fraction)."""
        video = self.create_video_with_thumbnail()
        created_at = self.list_videos().json()[0]['created_at']
        self.assertTrue(created_at.endswith('Z'))
        self.assertTrue(created_at.startswith(video.created_at.strftime('%Y-%m-%dT%H:%M:%S')))

    def test_empty_list(self):
        """Without videos the response is an empty list."""
        response = self.list_videos()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_all_videos_without_pagination(self):
        """Every video is returned in one plain list."""
        for number in range(30):
            self.create_video_with_thumbnail(title=f'Video {number}')
        self.assertEqual(len(self.list_videos().json()), 30)

    def test_response_is_json(self):
        """The response is sent as JSON."""
        self.assertEqual(self.list_videos()['Content-Type'], 'application/json')

    def test_url_path_matches_endpoint_doc(self):
        """The route is exactly /api/video/."""
        self.assertEqual(reverse('video-list'), '/api/video/')


class VideoListOrderTests(VideoListTestMixin, APITestCase):
    """Newest videos come first (the frontend shows VIDEOS[0] as hero)."""

    def test_sorted_by_created_at_descending(self):
        """The list is ordered by -created_at, not by id or title."""
        old = self.create_video_with_thumbnail(title='A old')
        new = self.create_video_with_thumbnail(title='C new')
        middle = self.create_video_with_thumbnail(title='B middle')
        self.set_created_at(old, days_ago=10)
        self.set_created_at(middle, days_ago=5)
        ids = [item['id'] for item in self.list_videos().json()]
        self.assertEqual(ids, [new.pk, middle.pk, old.pk])

    def test_category_does_not_change_the_order(self):
        """Grouping by genre is the frontend's job, the API sorts by date."""
        drama = self.create_video_with_thumbnail(category='Drama')
        comedy = self.create_video_with_thumbnail(category='Comedy')
        self.set_created_at(drama, days_ago=1)
        ids = [item['id'] for item in self.list_videos().json()]
        self.assertEqual(ids, [comedy.pk, drama.pk])

    def test_same_created_at_is_ordered_by_newest_id(self):
        """Equal timestamps get a stable order, the later upload first."""
        first = self.create_video_with_thumbnail()
        second = self.create_video_with_thumbnail()
        Video.objects.update(created_at=timezone.now())
        for _ in range(3):
            ids = [item['id'] for item in self.list_videos().json()]
            self.assertEqual(ids, [second.pk, first.pk])


class VideoListThumbnailTests(VideoListTestMixin, APITestCase):
    """thumbnail_url is an absolute URL to the media file."""

    def test_thumbnail_url_is_absolute(self):
        """Scheme, host and media path are included."""
        self.create_video_with_thumbnail('thumbnail/movie.jpg')
        url = self.list_videos().json()[0]['thumbnail_url']
        self.assertEqual(url, 'http://testserver/media/thumbnail/movie.jpg')

    @override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1'])
    def test_thumbnail_url_uses_request_host(self):
        """The host of the request is used, e.g. 127.0.0.1:8000."""
        self.create_video_with_thumbnail('thumbnail/movie.jpg')
        response = self.list_videos(HTTP_HOST='127.0.0.1:8000')
        url = response.json()[0]['thumbnail_url']
        self.assertEqual(url, 'http://127.0.0.1:8000/media/thumbnail/movie.jpg')

    def test_thumbnail_url_with_https(self):
        """Behind HTTPS the URL uses https as well."""
        self.create_video_with_thumbnail('thumbnail/movie.jpg')
        url = self.list_videos(secure=True).json()[0]['thumbnail_url']
        self.assertTrue(url.startswith('https://testserver/'))

    def test_special_characters_in_file_name_are_encoded(self):
        """Spaces in the file name are URL-encoded."""
        self.create_video_with_thumbnail('thumbnail/my movie.jpg')
        url = self.list_videos().json()[0]['thumbnail_url']
        self.assertEqual(url, 'http://testserver/media/thumbnail/my%20movie.jpg')

    def test_missing_thumbnail_is_null(self):
        """While the thumbnail job has not run yet, the value is null."""
        create_video()
        self.assertIsNone(self.list_videos().json()[0]['thumbnail_url'])


class VideoListAuthTests(VideoListTestMixin, APITestCase):
    """Only requests with a valid access token cookie get the list."""

    def assert_unauthorized(self, response):
        """Check for 401 and that no video data is returned."""
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIsInstance(response.json(), list)

    def test_no_cookie(self):
        """Without cookie the request is rejected."""
        self.create_video_with_thumbnail()
        self.client.cookies.clear()
        self.assert_unauthorized(self.list_videos())

    def test_empty_cookie(self):
        """An empty access token cookie counts as not logged in."""
        self.client.cookies['access_token'] = ''
        self.assert_unauthorized(self.list_videos())

    def test_garbage_cookie(self):
        """A random string is rejected."""
        self.client.cookies['access_token'] = 'not-a-jwt'
        self.assert_unauthorized(self.list_videos())

    def test_expired_access_token(self):
        """An expired access token is rejected (frontend refreshes it)."""
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=-timedelta(seconds=1))
        self.client.cookies['access_token'] = str(token)
        self.assert_unauthorized(self.list_videos())

    def test_refresh_token_in_access_cookie(self):
        """A refresh token has the wrong type for API access."""
        self.client.cookies['access_token'] = str(RefreshToken.for_user(self.user))
        self.assert_unauthorized(self.list_videos())

    def test_bearer_header_is_not_accepted(self):
        """Only the HttpOnly cookie authenticates, not an Authorization header."""
        token = self.client.cookies['access_token'].value
        self.client.cookies.clear()
        response = self.list_videos(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assert_unauthorized(response)

    def test_inactive_user(self):
        """A deactivated user's token no longer works."""
        self.user.is_active = False
        self.user.save()
        self.assert_unauthorized(self.list_videos())

    def test_deleted_user(self):
        """A deleted user's token no longer works."""
        self.user.delete()
        self.assert_unauthorized(self.list_videos())


class VideoListLoginFlowTests(VideoListTestMixin, APITestCase):
    """The list works with the cookies set by the auth endpoints."""

    def login(self):
        """Log in through the endpoint so the client stores the cookies."""
        self.client.cookies.clear()
        payload = {'email': 'user@example.com', 'password': 'pw'}
        self.client.post(reverse('login'), payload)

    def test_list_after_login(self):
        """The cookie from the login grants access."""
        self.login()
        self.assertEqual(self.list_videos().status_code, status.HTTP_200_OK)

    def test_list_after_logout_is_denied(self):
        """After logout the cookies are gone and the list returns 401."""
        self.login()
        self.client.post(reverse('logout'))
        self.assertEqual(self.list_videos().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_after_token_refresh(self):
        """The refreshed access token cookie grants access."""
        self.login()
        self.client.cookies['access_token'] = 'expired-or-broken'
        self.client.post(reverse('token_refresh'))
        self.assertEqual(self.list_videos().status_code, status.HTTP_200_OK)


class VideoListMethodTests(VideoListTestMixin, APITestCase):
    """The list is read-only."""

    def test_other_methods_are_not_allowed(self):
        """POST, PUT, PATCH and DELETE return 405 and change nothing."""
        self.create_video_with_thumbnail()
        for method in ['post', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse('video-list'))
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(Video.objects.count(), 1)

    def test_other_methods_without_login_return_401(self):
        """Without login the authentication fails before the method check."""
        self.client.cookies.clear()
        response = self.client.post(reverse('video-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class VideoListServerErrorTests(VideoListTestMixin, APITestCase):
    """Unexpected server errors return 500 as listed in the endpoint doc."""

    def test_database_error_returns_500(self):
        """A failing database query is answered with 500, not a broken list."""
        self.client.raise_request_exception = False
        with patch('video_app.api.views.VideoListView.get_queryset',
                   side_effect=DatabaseError('database is down')):
            response = self.list_videos()
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_error_details_are_not_leaked(self):
        """The internal error message is not sent to the client."""
        self.client.raise_request_exception = False
        with patch('video_app.api.views.VideoListView.get_queryset',
                   side_effect=DatabaseError('secret connection string')):
            response = self.list_videos()
        self.assertNotIn(b'secret connection string', response.content)
