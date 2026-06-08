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


# ── Egress (Recording) API ───────────────────────────────────────────

def start_room_recording(room_name):
    """Start a room composite egress recording.

    Returns dict with egress_id and file_path on success, None on failure.
    """
    url = f'{settings.LIVEKIT_HTTP_URL}/twirp/livekit.Egress/StartRoomCompositeEgress'
    token = _make_api_token(grants={'roomRecord': True})
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    timestamp = int(time.time())
    file_path = f'pp-portal/{room_name}-{timestamp}.mp4'
    payload = {
        'room_name': room_name,
        'file': {
            'filepath': file_path,
        },
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            'egress_id': data.get('egress_id', ''),
            'file_path': file_path,
        }
    except Exception as e:
        logger.error(f'LiveKit start_room_recording failed for {room_name}: {e}')
        return None


def stop_recording(egress_id):
    """Stop an active egress recording.

    Returns True on success, False on failure.
    """
    url = f'{settings.LIVEKIT_HTTP_URL}/twirp/livekit.Egress/StopEgress'
    token = _make_api_token(grants={'roomRecord': True})
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    payload = {'egress_id': egress_id}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'LiveKit stop_recording failed for {egress_id}: {e}')
        return False


def list_egress(room_name=None):
    """List active egress items, optionally filtered by room name.

    Returns list of egress dicts on success, empty list on failure.
    """
    url = f'{settings.LIVEKIT_HTTP_URL}/twirp/livekit.Egress/ListEgress'
    token = _make_api_token(grants={'roomRecord': True})
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    payload = {}
    if room_name:
        payload['room_name'] = room_name
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get('items', [])
    except Exception as e:
        logger.error(f'LiveKit list_egress failed: {e}')
        return []
