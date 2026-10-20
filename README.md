# Videoflix – Video Streaming Backend

A REST API backend for a video streaming platform with adaptive HLS playback, developed as the final backend project within the Developer Akademie.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-6.1-092E20?style=flat-square&logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.18-A30000?style=flat-square&logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-Queue-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-HLS-007808?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)

A live demo is coming soon.

The matching frontend is provided by the Developer Akademie: [Developer-Akademie-Backendkurs/project.Videoflix](https://github.com/Developer-Akademie-Backendkurs/project.Videoflix)

---

## Table of Contents
1. [Introduction](#introduction)
2. [Features](#features)
3. [Tech Stack](#tech-stack)
4. [Installation & Setup](#installation--setup)
5. [Environment Variables](#environment-variables)
6. [Uploading Videos](#uploading-videos)
7. [API Endpoints](#api-endpoints)
8. [Project Structure](#project-structure)
9. [Testing](#testing)
10. [Troubleshooting](#troubleshooting)
11. [Author](#author)

---

## Introduction

**Videoflix** is a Netflix-inspired streaming platform. This repository contains the **backend only**: a Django REST API that handles user accounts and delivers videos as adaptive **HLS streams** in 480p, 720p and 1080p.

Videos are uploaded through the Django admin. Thumbnail creation and HLS conversion run automatically in the background with **Django RQ** and **FFmpeg**. The whole backend starts with a single **Docker Compose** command.

---

## Features

* **User Registration & Activation:** Registration with email and password; new accounts stay inactive until the HTML activation email link is opened.
* **Secure Authentication:** Login with JWT stored in HttpOnly cookies, token refresh and logout with refresh token blacklisting. Error messages are kept generic and never reveal whether an account exists.
* **Password Reset:** Reset email with a link valid for 24 hours; the old password becomes invalid immediately.
* **Background Video Processing:** After an upload, a thumbnail (taken from 10 % into the video) and HLS renditions in 480p, 720p and 1080p are created automatically.
* **Adaptive Streaming:** Video list for the dashboard (newest first), HLS playlists and segments per resolution – protected by the login cookie.
* **Caching:** The video list is served from Redis and refreshed automatically whenever a video is added, changed or deleted.
* **Automatic Cleanup:** Files of deleted or replaced videos are removed; half-finished conversions are never served.
* **Test-Driven Development:** Every endpoint and job is covered by tests (100 % coverage), including end-to-end tests with real FFmpeg conversions.

---

## Tech Stack

* **Framework:** Django 6.1 with Django REST Framework
* **Authentication:** djangorestframework-simplejwt (HttpOnly cookies, token blacklist)
* **Database:** PostgreSQL
* **Cache & Job Queue:** Redis with Django RQ
* **Video Processing:** FFmpeg (HLS with H.264 video and AAC audio)
* **Email:** Django mail with HTML templates (SMTP)
* **Deployment:** Docker & Docker Compose, gunicorn, whitenoise
* **Testing:** pytest, pytest-django, pytest-cov

---

## Installation & Setup

### Prerequisites
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)
* [Git](https://git-scm.com/)
* An SMTP account for sending emails (e.g. [Mailtrap](https://mailtrap.io/) for testing)

FFmpeg, PostgreSQL and Redis run inside Docker and do not need to be installed locally.

### 1. Clone the repository
```bash
git clone https://github.com/JensBaumannDev/Videoflix.git
cd Videoflix
```

### 2. Create the environment file
```bash
cp .env.template .env
```
Fill in your own values (see [Environment Variables](#environment-variables)) and add the two project-specific variables at the end of `.env`:
```text
FRONTEND_URL=http://127.0.0.1:5501
CORS_ALLOWED_ORIGINS=http://127.0.0.1:5501,http://localhost:5501
```

### 3. Build and start the containers
```bash
docker compose up --build
```
The entrypoint waits for PostgreSQL, collects static files, runs the migrations, creates the superuser from `.env` and starts the RQ worker and gunicorn.

### 4. Open the backend
* API: `http://127.0.0.1:8000/api/`
* Admin panel: `http://127.0.0.1:8000/admin/` (login with `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`)
* Job dashboard: `http://127.0.0.1:8000/django-rq/`

### 5. Start the frontend
Clone the [frontend](https://github.com/Developer-Akademie-Backendkurs/project.Videoflix), open it in VS Code and start it with **Live Server** (port 5501). Open it via `http://127.0.0.1:5501` – **not** `localhost`, otherwise the browser does not send the login cookies to the backend.

Stop the containers with `docker compose down`. `docker compose down -v` additionally deletes the database and all uploaded videos.

---

## Environment Variables

Variables from `.env.template`:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `DJANGO_SUPERUSER_USERNAME` | Admin user created on start | `admin` |
| `DJANGO_SUPERUSER_PASSWORD` | Password of the admin user | – |
| `DJANGO_SUPERUSER_EMAIL` | Email of the admin user | `admin@example.com` |
| `SECRET_KEY` | Django secret key – use your own random value | – |
| `DEBUG` | `True` for development (also serves media files) | `True` |
| `ALLOWED_HOSTS` | Comma-separated host names | `localhost,127.0.0.1` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins | `http://127.0.0.1:5501` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL credentials | – |
| `DB_HOST` / `DB_PORT` | PostgreSQL host and port | `db` / `5432` |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | Redis for Django RQ | `redis` / `6379` / `0` |
| `REDIS_LOCATION` | Redis URL for the cache | `redis://redis:6379/1` |
| `EMAIL_HOST` / `EMAIL_PORT` | SMTP server | `sandbox.smtp.mailtrap.io` / `587` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | SMTP credentials | – |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | Encryption (enable only one) | `True` / `False` |
| `DEFAULT_FROM_EMAIL` | Sender address of all emails | `noreply@example.com` |

Additional variables of this project (not in the template):

| Variable | Description | Default |
| :--- | :--- | :--- |
| `FRONTEND_URL` | Fallback base URL for the links in activation and reset emails. Normally the links point to the frontend the request came from (its `Origin`, if listed in `CORS_ALLOWED_ORIGINS`) | `http://127.0.0.1:5501` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated origins allowed to call the API with cookies | Live Server on ports 5500 / 5501 |
| `COOKIE_SECURE` | `True` behind HTTPS: cookies are only sent over encrypted connections | `False` |
| `BEHIND_HTTPS_PROXY` | `True` behind nginx with HTTPS: trust `X-Forwarded-Proto` for https URLs | `False` |
| `ACCESS_TOKEN_LIFETIME_MINUTES` | Lifetime of the access token in minutes | `30` |

The `.env` file contains secrets and is never committed.

---

## Uploading Videos

1. Open the admin panel → **Videos** → **Add video**.
2. Enter title, description and category and choose a video file (`mp4`, `mov`, `mkv`, `webm`, `avi`). Leave **Thumbnail** empty.
3. After saving, two background jobs start automatically:
   * **Thumbnail:** FFmpeg picks a representative frame from 10 % into the video.
   * **HLS conversion:** one rendition each for 480p, 720p and 1080p with 10-second segments.
4. Follow the progress with `docker compose logs -f web` or in the job dashboard. A 10-minute film takes a few minutes.

Only upload videos you are allowed to publish, e.g. the [Blender open movies](https://studio.blender.org/films/) (CC BY – credit them in the description) or royalty-free clips.

Generated files are stored in the Docker volume `videoflix_media`:
```text
media/
├── videos/<file>                    # Original upload
├── thumbnail/<name>_<suffix>.jpg    # Generated thumbnail (new name on every update)
└── hls/<video id>/<resolution>/     # index.m3u8, 000.ts, 001.ts, ...
```

---

## API Endpoints

All endpoints are prefixed with `/api/`.

### Authentication

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| POST | `register/` | – | Create an inactive user and send the activation email |
| GET | `activate/<uidb64>/<token>/` | – | Activate the account |
| POST | `login/` | – | Log in; sets the HttpOnly cookies `access_token` and `refresh_token` |
| POST | `logout/` | Refresh cookie | Blacklist the refresh token and delete both cookies |
| POST | `token/refresh/` | Refresh cookie | Set a new `access_token` cookie |
| POST | `password_reset/` | – | Send a reset email (always answers 200) |
| POST | `password_confirm/<uidb64>/<token>/` | – | Set a new password |

### Videos

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| GET | `video/` | Access cookie | All videos, newest first, with absolute `thumbnail_url` |
| GET | `video/<id>/<resolution>/index.m3u8` | Access cookie | HLS playlist for `480p`, `720p` or `1080p` |
| GET | `video/<id>/<resolution>/<segment>/` | Access cookie | HLS segment such as `000.ts` (trailing slash optional) |

### Token Lifetimes
* **Access token:** 30 minutes (the frontend refreshes it every 20 minutes)
* **Refresh token:** 1 day, blacklisted on logout
* **Activation and password reset links:** 24 hours

---

## Project Structure

A high-level directory tree of the Django project:

```text
backend/
├── core/                        # Django project: settings, root URLs, WSGI
├── auth_app/                    # Accounts: register, activate, login, logout, password reset
│   ├── api/                     # Serializers, views, URLs, cookie JWT authentication
│   ├── templates/auth_app/      # HTML and text email templates
│   ├── tasks.py                 # Email jobs for Django RQ
│   ├── utils.py                 # Tokens, links, emails, cookies
│   └── tests/                   # Tests per endpoint
├── video_app/                   # Videos: model, admin, processing, streaming
│   ├── api/                     # Serializers, views, URLs for list, playlists, segments
│   ├── signals.py               # Start processing after upload, clean up files
│   ├── tasks.py                 # Thumbnail and HLS jobs for Django RQ
│   ├── utils.py                 # FFmpeg commands, file paths, file responses
│   └── tests/                   # Tests incl. real FFmpeg conversions
├── backend.Dockerfile           # Docker setup provided by the Developer Akademie
├── backend.entrypoint.sh        # Container start script (Developer Akademie)
├── docker-compose.yml           # Services: web, db, redis (Developer Akademie)
├── .env.template                # Template for the environment variables
├── requirements.txt             # Python dependencies
└── README.md                    # Project documentation
```

---

## Testing

The tests run inside the container against PostgreSQL:
```bash
docker compose exec web pytest
```
A coverage report is printed automatically. Some tests convert tiny generated videos with the real FFmpeg; they are skipped automatically where FFmpeg is not installed.

**Code style:** PEP-8 with a maximum line length of 99 characters, functions with at most 14 lines and one task each, docstrings for all classes and functions.

---

## Troubleshooting

| Problem | Solution |
| :--- | :--- |
| Code changes in `tasks.py` or `utils.py` have no effect on jobs | The RQ worker does not reload code. Run `docker compose restart web`. |
| No activation or reset email arrives | Check `docker compose logs web` for failed jobs and the SMTP values in `.env`. Reset emails are only sent to active accounts. |
| Dashboard requests return 401 | The access token expired (e.g. after reloading the page). Log out and in again. |
| Logged in, but the API ignores the cookies | Open the frontend via `127.0.0.1`, not `localhost`. |
| A video does not play yet | Wait until the HLS job has finished (see logs); the playlist returns 404 until then. |

---

## Author

| Developer | GitHub | LinkedIn | Portfolio |
| :--- | :--- | :--- | :--- |
| **Jens Baumann** | [@JensBaumannDev](https://github.com/JensBaumannDev) | [Jens Baumann](https://www.linkedin.com/in/jens-baumann-7a6866315/) | [jensbaumann.com](https://jensbaumann.com/) |

---
Developed as part of the software engineering curriculum at the [Developer Akademie](https://developerakademie.com/).
