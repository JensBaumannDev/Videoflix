from unittest import skipUnless

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from video_app.tasks import convert_to_hls
from video_app.tests.helpers import (
    FFMPEG_AVAILABLE,
    TempMediaMixin,
    create_video,
    generate_video_upload,
)
from video_app.utils import HLS_RESOLUTIONS, build_hls_dir

PLAYLIST = b'#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:10.0,\n000.ts\n#EXT-X-ENDLIST\n'
SEGMENT = b'\x47fake-transport-stream'
MANIFEST_TYPE = 'application/vnd.apple.mpegurl'
SEGMENT_TYPE = 'video/MP2T'


class StreamTestMixin(TempMediaMixin):
    """A logged-in user and a video with fake HLS files on disk."""

    def setUp(self):
        """Log in a user and write playlist and segment for all resolutions."""
        super().setUp()
        self.user = User.objects.create_user('user@example.com', 'user@example.com', 'pw')
        self.client.cookies['access_token'] = str(AccessToken.for_user(self.user))
        self.video = create_video()
        for resolution in HLS_RESOLUTIONS:
            self.write_hls_files(self.video.pk, resolution)

    def write_hls_files(self, video_id, resolution):
        """Write a playlist and one segment like the HLS task does."""
        folder = build_hls_dir(video_id, resolution)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'index.m3u8').write_bytes(PLAYLIST)
        (folder / '000.ts').write_bytes(SEGMENT)

    def manifest_url(self, video_id=None, resolution='480p'):
        """Return the playlist URL as hls.js requests it."""
        return f'/api/video/{video_id or self.video.pk}/{resolution}/index.m3u8'

    def segment_url(self, segment='000.ts', video_id=None, resolution='480p', slash=False):
        """Return the segment URL with or without trailing slash."""
        url = f'/api/video/{video_id or self.video.pk}/{resolution}/{segment}'
        return url + '/' if slash else url

    def read_body(self, response):
        """Return the full body of a (streaming) file response."""
        return b''.join(response.streaming_content)


class ManifestSuccessTests(StreamTestMixin, APITestCase):
    """The playlist is delivered for every resolution."""

    def test_returns_200_with_playlist_content(self):
        """The body is exactly the index.m3u8 file."""
        response = self.client.get(self.manifest_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.read_body(response), PLAYLIST)

    def test_content_type_matches_endpoint_doc(self):
        """Content-Type is application/vnd.apple.mpegurl."""
        response = self.client.get(self.manifest_url())
        self.assertEqual(response['Content-Type'], MANIFEST_TYPE)

    def test_all_resolutions(self):
        """480p, 720p and 1080p are available."""
        for resolution in HLS_RESOLUTIONS:
            with self.subTest(resolution=resolution):
                response = self.client.get(self.manifest_url(resolution=resolution))
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_file_is_streamed(self):
        """Files are streamed instead of loaded into memory."""
        self.assertTrue(self.client.get(self.manifest_url()).streaming)

    def test_url_path_matches_endpoint_doc(self):
        """The route is /api/video/<id>/<resolution>/index.m3u8."""
        url = reverse('video-manifest', args=[1, '480p'])
        self.assertEqual(url, '/api/video/1/480p/index.m3u8')


class ManifestNotFoundTests(StreamTestMixin, APITestCase):
    """Missing video, resolution or playlist returns 404."""

    def assert_not_found(self, url):
        """Check that the URL answers with 404."""
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_video_id(self):
        """A video id without database row is 404."""
        self.assert_not_found(self.manifest_url(video_id=999999))

    def test_deleted_video_with_leftover_files(self):
        """Files of a deleted video are not served anymore."""
        video_id = self.video.pk
        self.video.delete()
        self.write_hls_files(video_id, '480p')
        self.assert_not_found(self.manifest_url(video_id=video_id))

    def test_conversion_not_finished(self):
        """A video without playlist yet is 404."""
        video = create_video()
        self.assert_not_found(self.manifest_url(video_id=video.pk))

    def test_unsupported_resolutions(self):
        """Only 480p, 720p and 1080p are allowed, case-sensitive."""
        for resolution in ['360p', '2160p', '480P', '480', 'abc']:
            with self.subTest(resolution=resolution):
                self.assert_not_found(self.manifest_url(resolution=resolution))

    def test_path_traversal_in_resolution(self):
        """'..' or encoded slashes cannot leave the HLS folder."""
        for resolution in ['..', '.', '%2e%2e', '..%2F..%2F..']:
            with self.subTest(resolution=resolution):
                self.assert_not_found(self.manifest_url(resolution=resolution))

    def test_non_numeric_video_id(self):
        """The id must be an integer."""
        self.assert_not_found('/api/video/abc/480p/index.m3u8')


class SegmentSuccessTests(StreamTestMixin, APITestCase):
    """Segments are delivered with and without trailing slash."""

    def test_returns_200_with_segment_content(self):
        """The body is exactly the .ts file."""
        response = self.client.get(self.segment_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.read_body(response), SEGMENT)

    def test_content_type_matches_endpoint_doc(self):
        """Content-Type is video/MP2T."""
        response = self.client.get(self.segment_url())
        self.assertEqual(response['Content-Type'], SEGMENT_TYPE)

    def test_without_trailing_slash_like_hls_js(self):
        """hls.js resolves 000.ts relative to the playlist, without slash."""
        response = self.client.get(self.segment_url(slash=False))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_with_trailing_slash_like_endpoint_doc(self):
        """The documented form with trailing slash works too."""
        response = self.client.get(self.segment_url(slash=True))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_no_redirect_is_used(self):
        """The segment is served directly, not via APPEND_SLASH redirect."""
        response = self.client.get(self.segment_url(slash=False))
        self.assertNotIn(response.status_code, [301, 302, 308])

    def test_segment_numbers_above_999(self):
        """Very long films produce 1000.ts and more."""
        folder = build_hls_dir(self.video.pk, '480p')
        (folder / '1000.ts').write_bytes(SEGMENT)
        response = self.client.get(self.segment_url('1000.ts'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_url_path_matches_endpoint_doc(self):
        """The route is /api/video/<id>/<resolution>/<segment>/."""
        url = reverse('video-segment', args=[1, '480p', '000.ts'])
        self.assertIn(url, ['/api/video/1/480p/000.ts', '/api/video/1/480p/000.ts/'])


class SegmentNotFoundTests(StreamTestMixin, APITestCase):
    """Missing or invalid segments return 404."""

    def assert_not_found(self, url):
        """Check that the URL answers with 404."""
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_segment(self):
        """A segment number that does not exist is 404."""
        self.assert_not_found(self.segment_url('999.ts'))

    def test_unknown_video_id(self):
        """A video id without database row is 404."""
        self.assert_not_found(self.segment_url(video_id=999999))

    def test_unsupported_resolution(self):
        """Segments are only served for the three resolutions."""
        self.assert_not_found(self.segment_url(resolution='360p'))

    def test_invalid_segment_names(self):
        """Only names like 000.ts are served, no other files."""
        for name in ['abc.ts', '.ts', '00.ts', '000.mp4', '000.ts.bak', '-01.ts', '..']:
            with self.subTest(name=name):
                self.assert_not_found(self.segment_url(name))

    def test_playlist_is_not_served_as_segment(self):
        """index.m3u8 with trailing slash does not go through the segment route."""
        self.assert_not_found(self.segment_url('index.m3u8', slash=True))

    def test_other_files_in_folder_are_not_served(self):
        """A non-segment file next to the segments stays private."""
        folder = build_hls_dir(self.video.pk, '480p')
        (folder / 'notes.txt').write_bytes(b'secret')
        self.assert_not_found(self.segment_url('notes.txt'))

    def test_path_traversal(self):
        """Encoded '..' and slashes cannot reach files outside the folder."""
        for url in [self.segment_url('..%2F..%2F..%2Fvideos%2Fmovie.mp4'),
                    self.segment_url('000.ts', resolution='..'),
                    self.segment_url('%2e%2e%2F720p%2F000.ts')]:
            with self.subTest(url=url):
                self.assert_not_found(url)

    def test_segment_of_deleted_video(self):
        """Leftover segments of a deleted video are not served."""
        video_id = self.video.pk
        self.video.delete()
        self.write_hls_files(video_id, '480p')
        self.assert_not_found(self.segment_url(video_id=video_id))


class StreamAuthTests(StreamTestMixin, APITestCase):
    """Playlist and segments need a valid access token cookie."""

    def urls(self):
        """Return one playlist and both segment URL forms."""
        return [self.manifest_url(), self.segment_url(), self.segment_url(slash=True)]

    def test_without_cookie(self):
        """Without login every stream URL returns 401."""
        self.client.cookies.clear()
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_with_invalid_cookie(self):
        """A broken token returns 401."""
        self.client.cookies['access_token'] = 'garbage'
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_video_without_login_is_401_not_404(self):
        """Authentication is checked first, nothing is revealed."""
        self.client.cookies.clear()
        response = self.client.get(self.manifest_url(video_id=999999))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_other_methods_are_not_allowed(self):
        """POST, PUT, PATCH and DELETE return 405 and delete nothing."""
        for method in ['post', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                for url in self.urls():
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 405)
        self.assertTrue((build_hls_dir(self.video.pk, '480p') / '000.ts').is_file())


@skipUnless(FFMPEG_AVAILABLE, 'FFmpeg is not installed')
class StreamRealConversionTests(TempMediaMixin, APITestCase):
    """End to end: convert a real video and stream it like hls.js."""

    def setUp(self):
        """Log in and convert a generated 25 second video."""
        super().setUp()
        user = User.objects.create_user('user@example.com', 'user@example.com', 'pw')
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))
        upload = generate_video_upload(self.media_root, duration='25', audio=True)
        self.video = create_video(video_file=upload)
        convert_to_hls(self.video.pk)

    def segment_names(self, resolution):
        """Return the segment names listed in the served playlist."""
        url = f'/api/video/{self.video.pk}/{resolution}/index.m3u8'
        playlist = b''.join(self.client.get(url).streaming_content).decode()
        return [line for line in playlist.splitlines() if line.endswith('.ts')]

    def test_every_listed_segment_can_be_loaded(self):
        """All segments named in each playlist are served with 200."""
        for resolution in HLS_RESOLUTIONS:
            names = self.segment_names(resolution)
            self.assertGreaterEqual(len(names), 2)
            for name in names:
                with self.subTest(resolution=resolution, segment=name):
                    url = f'/api/video/{self.video.pk}/{resolution}/{name}'
                    self.assertEqual(self.client.get(url).status_code, 200)
