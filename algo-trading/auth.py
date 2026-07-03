import os
import datetime
import jwt
from functools import wraps
from flask import request, redirect, g, jsonify, url_for
from db import get_user_by_id

JWT_SECRET = os.environ.get("JWT_SECRET_KEY", "super-secret-algo-trading-key-12345")
JWT_ALGORITHM = "HS256"

def generate_token(user_id):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "exp": now + datetime.timedelta(hours=24),
        "iat": now,
        "sub": str(user_id)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError):
        return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Try to read token from cookie
        token = request.cookies.get("auth_token")
        user = None
        
        if token:
            user_id = decode_token(token)
            if user_id:
                user = get_user_by_id(user_id)
        
        if not user:
            # Check if this is an API route or expects JSON
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"status": "error", "message": "Authentication required"}), 401
            return redirect(url_for("login"))
            
        g.user = user
        return f(*args, **kwargs)
    return decorated_function
