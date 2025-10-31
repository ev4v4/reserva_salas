import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

username = "gabriel_evaristo"
email = "gabrieljoseevaristo@gmail.com"
password = "Callink@2314"

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print("✅ Superuser criado com sucesso!")
else:
    print("⚠️ Superuser já existe, nenhum novo criado.")
