"""
Django settings for pp_portal project.
PP Translation Services Portal for UAE Federal Public Prosecution.
"""

from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present
_env_path = BASE_DIR / '.env'
if _env_path.is_file():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ.setdefault(key.strip(), value.strip())

# On Windows, WeasyPrint needs GTK/Pango DLLs that are not on the default search
# path. Set GTK_BIN_DIR (e.g. in .env) to the directory holding libgobject-2.0-0.dll
# — typically C:\msys64\mingw64\bin. It is prepended to PATH so it wins over
# unrelated copies of those DLLs shipped by other applications.
_gtk_bin_dir = os.environ.get('GTK_BIN_DIR', '')
if _gtk_bin_dir and os.path.isdir(_gtk_bin_dir):
    os.environ['PATH'] = _gtk_bin_dir + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(_gtk_bin_dir)


def _env_bool(name, default):
    return os.environ.get(name, str(default)).lower() in ('true', '1', 'yes')


# Fail closed: debug mode must be switched on explicitly (see .env.example).
DEBUG = _env_bool('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-change-in-production'
)
if not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-')):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be a long random value when DJANGO_DEBUG is off.'
    )

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')

CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if origin
]

# HTTPS hardening, production only. TLS is expected to terminate at nginx,
# which must send "proxy_set_header X-Forwarded-Proto $scheme;".
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start short; raise to 31536000 once HTTPS is known to be stable.
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '3600'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# HSTS preload is a near-permanent browser commitment; opt in deliberately.
SILENCED_SYSTEM_CHECKS = ['security.W021']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_htmx',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'pp_portal.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'pp_portal.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3'),
        'NAME': os.environ.get('DB_NAME', str(BASE_DIR / 'db.sqlite3')),
        'USER': os.environ.get('DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', ''),
        'PORT': os.environ.get('DB_PORT', ''),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization - Arabic RTL
LANGUAGE_CODE = 'ar'
TIME_ZONE = 'Asia/Dubai'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Auth
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Email
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@smartworld.ae')

# Contract
CONTRACT_TOTAL_VALUE = 500_000  # AED
CONTRACT_NUMBER = '3922001306'
VAT_RATE = 5  # percent

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# LiveKit Video Conference
LIVEKIT_API_KEY = os.environ.get('LIVEKIT_API_KEY', '')
LIVEKIT_API_SECRET = os.environ.get('LIVEKIT_API_SECRET', '')
LIVEKIT_WS_URL = os.environ.get('LIVEKIT_WS_URL', 'wss://livekit.swlt.ae')
LIVEKIT_HTTP_URL = os.environ.get('LIVEKIT_HTTP_URL', 'http://127.0.0.1:7880')

# Recording storage (egress container writes here)
RECORDING_ROOT = os.environ.get('RECORDING_ROOT', '/var/www/gfad-portal/storage/app/recordings')

# OpenAI API (used for caption translation)
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
