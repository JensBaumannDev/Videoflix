import subprocess
from pathlib import Path

from video_app.models import Video

THUMBNAIL_FOLDER = 'thumbnail'
THUMBNAIL_MAX_WIDTH = 640


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
