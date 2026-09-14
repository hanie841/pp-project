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
    'glossary',
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
                'glossary.context_processors.glossary_nav',
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

# Base address for links in e-mails
SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')

# Work-order documents (files to translate and delivered translations). They are
# private: never under MEDIA_ROOT, which nginx serves without login. Downloads go
# through authenticated views (X-Accel-Redirect locally, presigned URLs on S3).
DOCUMENT_STORAGE = os.environ.get('DOCUMENT_STORAGE', 'local')
PRIVATE_FILES_ROOT = os.environ.get('PRIVATE_FILES_ROOT', str(BASE_DIR / 'private'))
DOCUMENT_MAX_UPLOAD_SIZE = int(os.environ.get('DOCUMENT_MAX_UPLOAD_SIZE', 25 * 1024 * 1024))
DOCUMENT_MAX_ORDER_TOTAL_SIZE = int(
    os.environ.get('DOCUMENT_MAX_ORDER_TOTAL_SIZE', 500 * 1024 * 1024)
)
DOCUMENT_ALLOWED_EXTENSIONS = [
    ext.strip().lower().lstrip('.')
    for ext in os.environ.get(
        'DOCUMENT_ALLOWED_EXTENSIONS',
        'pdf,doc,docx,xls,xlsx,ppt,pptx,rtf,txt,jpg,jpeg,png,tif,tiff',
    ).split(',')
    if ext.strip()
]
# ClamAV scanning: 'off', or 'required' (uploads are refused while clamd is down).
DOCUMENT_VIRUS_SCAN = os.environ.get('DOCUMENT_VIRUS_SCAN', 'off')
CLAMD_ADDRESS = os.environ.get('CLAMD_ADDRESS', 'unix:///var/run/clamav/clamd.ctl')
# Days after approval before purge_documents may delete a completed order's
# files. Unset keeps files indefinitely.
DOCUMENT_RETENTION_DAYS = (
    int(os.environ['DOCUMENT_RETENTION_DAYS'])
    if os.environ.get('DOCUMENT_RETENTION_DAYS') else None
)

if DOCUMENT_VIRUS_SCAN not in ('off', 'required'):
    raise ImproperlyConfigured("DOCUMENT_VIRUS_SCAN must be 'off' or 'required'.")

if DOCUMENT_STORAGE == 'local':
    _DOCUMENTS_BACKEND = {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
        'OPTIONS': {'location': PRIVATE_FILES_ROOT},
    }
elif DOCUMENT_STORAGE == 's3':
    if not os.environ.get('AWS_STORAGE_BUCKET_NAME'):
        raise ImproperlyConfigured('DOCUMENT_STORAGE=s3 requires AWS_STORAGE_BUCKET_NAME.')
    # Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY or the instance role.
    _DOCUMENTS_BACKEND = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': os.environ['AWS_STORAGE_BUCKET_NAME'],
            'region_name': os.environ.get('AWS_S3_REGION_NAME') or None,
            'location': 'documents',
            'default_acl': 'private',
            'querystring_auth': True,
            'querystring_expire': 60,
            'file_overwrite': False,
            'object_parameters': {'ServerSideEncryption': 'AES256'},
        },
    }
else:
    raise ImproperlyConfigured("DOCUMENT_STORAGE must be 'local' or 's3'.")

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    'documents': _DOCUMENTS_BACKEND,
}

# OpenAI API (used for caption translation)
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# Add approved glossary terms that occur in a caption to its translation prompt
GLOSSARY_IN_CAPTIONS = _env_bool('GLOSSARY_IN_CAPTIONS', True)
