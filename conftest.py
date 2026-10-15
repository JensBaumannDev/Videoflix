from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def use_plain_static_storage(settings):
    """Serve static files without the whitenoise manifest during tests.

    The manifest only exists after collectstatic, so admin pages would
    otherwise fail in tests depending on whether collectstatic ran before.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }


@pytest.fixture(autouse=True)
def block_real_rq_jobs():
    """Prevent tests from putting real jobs into the Redis queue."""
    with patch('django_rq.enqueue') as enqueue:
        yield enqueue
