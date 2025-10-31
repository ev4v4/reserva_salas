from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Profile

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Garante que cada User tenha um Profile único"""
    if created:
        # 🔒 Cria somente se não existir
        Profile.objects.get_or_create(user=instance)
