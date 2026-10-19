import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

from video_app.models import Video

THUMBNAIL_FOLDER = 'thumbnail'
THUMBNAIL_MAX_WIDTH = 640
HLS_FOLDER = 'hls'
HLS_SEGMENT_SECONDS = 10
HLS_RESOLUTIONS = {'480p': 480, '720p': 720, '1080p': 1080}
HLS_JOB_TIMEOUT = 60 * 60 * 3
HLS_PLAYLIST_NAME = 'index.m3u8'
HLS_SEGMENT_PATTERN = re.compile(r'\d{3,}\.ts')
MANIFEST_CONTENT_TYPE = 'application/vnd.apple.mpegurl'
SEGMENT_CONTENT_TYPE = 'video/MP2T'


class FfmpegError(RuntimeError):
    """Raised when FFmpeg fails; the message contains FFmpeg's error output."""


def build_thumbnail_name(video):
    """Return the storage name of the thumbnail, e.g. thumbnail/movie.jpg."""
    return f'{THUMBNAIL_FOLDER}/{Path(video.video_file.name).stem}.jpg'


def has_source_file(video):
    """Return whether the video has a file that exists on disk."""
    return bool(video.video_file) and Path(video.video_file.path).is_file()


def video_file_changed(video):
    """Return whether the video is new or got another file than in the database."""
    stored_names = Video.objects.filter(pk=video.pk).values_list('video_file', flat=True)
    return stored_names.first() != video.video_file.name


def run_ffmpeg(arguments):
    """Run FFmpeg quietly; raise FfmpegError with FFmpeg's message if it fails."""
    command = ['ffmpeg', '-y', '-loglevel', 'error', *arguments]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise FfmpegError(build_ffmpeg_error_message(error)) from error


def build_ffmpeg_error_message(error):
    """Return FFmpeg's error output, or the exit code if there is none."""
    if error.stderr and error.stderr.strip():
        return error.stderr.strip()
    return f'FFmpeg failed with exit code {error.returncode}'


def extract_thumbnail(source_path, output_path):
    """Save one representative frame of the video as JPEG, at most 640 px wide."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_filter = f"thumbnail,scale='min({THUMBNAIL_MAX_WIDTH},iw)':-2"
    run_ffmpeg(['-i', str(source_path), '-vf', video_filter,
                '-frames:v', '1', str(output_path)])


def build_hls_dir(video_id, resolution):
    """Return the folder for the HLS files, e.g. MEDIA_ROOT/hls/1/480p."""
    return Path(settings.MEDIA_ROOT) / HLS_FOLDER / str(video_id) / resolution


def build_hls_command(source_path, output_dir, height):
    """Return the FFmpeg arguments for one HLS rendition with 000.ts segments."""
    return [
        '-i', str(source_path), '-map', '0:v:0', '-map', '0:a:0?',
        '-vf', f'scale=-2:{height}', '-c:v', 'libx264', '-preset', 'veryfast',
        '-crf', '23', '-pix_fmt', 'yuv420p',
        '-force_key_frames', f'expr:gte(t,n_forced*{HLS_SEGMENT_SECONDS})',
        '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-f', 'hls', '-hls_time', str(HLS_SEGMENT_SECONDS), '-hls_playlist_type', 'vod',
        '-hls_segment_filename', str(output_dir / '%03d.ts'), str(output_dir / HLS_PLAYLIST_NAME),
    ]


def prepare_empty_dir(folder):
    """Delete old files in the folder and create it empty."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)


def convert_rendition(video, resolution, height):
    """Convert the video into one HLS resolution."""
    output_dir = build_hls_dir(video.pk, resolution)
    prepare_empty_dir(output_dir)
    run_ffmpeg(build_hls_command(video.video_file.path, output_dir, height))


def find_hls_file(video_id, resolution, file_name):
    """Return the path of an HLS file of an existing video, or None."""
    if resolution not in HLS_RESOLUTIONS:
        return None
    if not Video.objects.filter(pk=video_id).exists():
        return None
    file_path = build_hls_dir(video_id, resolution) / file_name
    return file_path if file_path.is_file() else None


def find_hls_segment(video_id, resolution, segment):
    """Return the path of a segment like 000.ts, or None for any other name."""
    if not HLS_SEGMENT_PATTERN.fullmatch(segment):
        return None
    return find_hls_file(video_id, resolution, segment)


def build_file_response(file_path, content_type):
    """Stream the file with the given content type, or raise 404 if it is missing."""
    if file_path is None:
        raise Http404
    return FileResponse(file_path.open('rb'), content_type=content_type)
