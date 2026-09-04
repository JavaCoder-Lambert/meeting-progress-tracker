from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse


class RateLimitedLoginView(LoginView):
    max_failures = 5
    window_seconds = 300

    def _key(self):
        address = self.request.META.get("REMOTE_ADDR", "unknown")
        return f"login-failures:{address}"

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and cache.get(self._key(), 0) >= self.max_failures:
            return HttpResponse("登录失败次数过多，请五分钟后重试。", status=429)
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        current = cache.get(key, 0)
        cache.set(key, current + 1, self.window_seconds)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)
