import shutil
import subprocess
from pathlib import Path

from django.conf import settings

from video_app.models import Video

THUMBNAIL_FOLDER = 'thumbnail'
THUMBNAIL_MAX_WIDTH = 640
HLS_FOLDER = 'hls'
HLS_SEGMENT_SECONDS = 10
HLS_RESOLUTIONS = {'480p': 480, '720p': 720, '1080p': 1080}


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
    """Run FFmpeg quietly; raise CalledProcessError if it fails."""
    command = ['ffmpeg', '-y', '-loglevel', 'error', *arguments]
    subprocess.run(command, check=True, capture_output=True)


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
        '-hls_segment_filename', str(output_dir / '%03d.ts'), str(output_dir / 'index.m3u8'),
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
