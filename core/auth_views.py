from functools import wraps
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse


def login_client_address(request):
    address = request.META.get("REMOTE_ADDR", "unknown")
    try:
        peer = ip_address(address)
        if any(peer in ip_network(cidr) for cidr in settings.LOGIN_TRUSTED_PROXY_CIDRS):
            # Only Caddy's overwritten, single-address header is trusted. Never consume XFF.
            return str(ip_address(request.META.get("HTTP_X_TRACKER_CLIENT_IP", "")))
        return str(peer)
    except ValueError:
        return address


def rate_limited_login(view):
    """Share the same failure budget without replacing Django admin authentication."""
    @wraps(view)
    def protected(request, *args, **kwargs):
        key = f"login-failures:{login_client_address(request)}"
        if request.method == "POST" and cache.get(key, 0) >= 5:
            return HttpResponse("登录失败次数过多，请五分钟后重试。", status=429)
        response = view(request, *args, **kwargs)
        if request.method == "POST":
            if response.status_code == 302:
                cache.delete(key)
            elif response.status_code == 200:
                if not cache.add(key, 1, 300):
                    try:
                        cache.incr(key)
                    except ValueError:  # The fixed window expired during authentication.
                        cache.add(key, 1, 300)
        return response
    return protected


class RateLimitedLoginView(LoginView):
    @classmethod
    def as_view(cls, **initkwargs):
        return rate_limited_login(super().as_view(**initkwargs))
