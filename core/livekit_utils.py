"""LiveKit integration helpers: room creation and JWT token generation."""

import time
import jwt
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _make_api_token(grants=None, ttl=60):
    """Create a short-lived JWT for LiveKit server API calls."""
    now = int(time.time())
    claims = {
        'iss': settings.LIVEKIT_API_KEY,
        'sub': '',
        'nbf': now,
        'exp': now + ttl,
        'jti': f'api-{now}',
    }
    if grants:
        claims['video'] = grants
    return jwt.encode(claims, settings.LIVEKIT_API_SECRET, algorithm='HS256')


def create_room(room_name):
    """Create a LiveKit room via the Twirp HTTP API.

    Returns the room name on success, None on failure.
    """
    url = f'{settings.LIVEKIT_HTTP_URL}/twirp/livekit.RoomService/CreateRoom'
    token = _make_api_token(grants={'roomCreate': True})
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    payload = {
        'name': room_name,
        'empty_timeout': 600,      # 10 min before auto-close when empty
        'max_participants': 20,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get('name', room_name)
    except Exception as e:
        logger.error(f'LiveKit create_room failed for {room_name}: {e}')
        return None


def generate_join_token(room_name, participant_name, participant_identity):
    """Generate a JWT access token for joining a LiveKit room.

    Returns a signed JWT string.
    """
    now = int(time.time())
    claims = {
        'iss': settings.LIVEKIT_API_KEY,
        'sub': participant_identity,
        'nbf': now,
        'exp': now + 4 * 3600,  # 4 hours
        'jti': f'{participant_identity}-{now}',
        'name': participant_name,
        'video': {
            'room': room_name,
            'roomJoin': True,
            'canPublish': True,
            'canSubscribe': True,
        },
    }
    return jwt.encode(claims, settings.LIVEKIT_API_SECRET, algorithm='HS256')
