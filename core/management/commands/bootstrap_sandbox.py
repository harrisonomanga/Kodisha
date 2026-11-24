import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "On first start: create the admin login from environment variables and optionally load demo data."

    def handle(self, *args, **opts):
        User = get_user_model()
        name, pw = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
        if name and pw and not User.objects.filter(username=name).exists():
            User.objects.create_superuser(name, os.environ.get("ADMIN_EMAIL", ""), pw)
            self.stdout.write(f"Created admin login '{name}'.")
        if os.environ.get("SEED_DEMO") == "1":
            call_command("seed_demo")
