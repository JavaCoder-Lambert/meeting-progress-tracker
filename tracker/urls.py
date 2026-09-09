from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from core.auth_views import RateLimitedLoginView, rate_limited_login

urlpatterns = [
    path("admin/login/", rate_limited_login(admin.site.login)),
    path("admin/", admin.site.urls),
    path("accounts/login/", RateLimitedLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password-change/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("accounts/password-change/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("", include("core.urls")),
]
