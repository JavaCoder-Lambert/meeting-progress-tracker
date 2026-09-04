import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


PASSWORD_PLACEHOLDER = "请替换为强密码"


class Command(BaseCommand):
    help = "Create the first administrator from environment variables."

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Administrator already exists; nothing changed.")
            return
        username = os.getenv("ADMIN_USERNAME", "").strip()
        password = os.getenv("ADMIN_PASSWORD", "")
        if not username or not password:
            raise CommandError("ADMIN_USERNAME and ADMIN_PASSWORD are required")
        if password == PASSWORD_PLACEHOLDER:
            raise CommandError("ADMIN_PASSWORD must not use the .env.example placeholder")
        if len(set(password)) < 4:
            raise CommandError(
                "ADMIN_PASSWORD must contain at least 4 unique characters"
            )
        candidate = User(username=username)
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            raise CommandError(
                f"ADMIN_PASSWORD does not meet password requirements: {'; '.join(exc.messages)}"
            ) from exc
        User.objects.create_superuser(username=username, password=password)
        self.stdout.write(self.style.SUCCESS(f"Administrator {username} created."))
