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
