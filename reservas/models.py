from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.timezone import make_aware, is_naive
from datetime import datetime, timedelta
from dateutil.rrule import rrulestr
from django.dispatch import receiver
from django.db.models.signals import post_save


# =============================
# Salas
# =============================
class Room(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


# =============================
# Perfil do Usuário (Função + Foto + Telefone)
# =============================
class Profile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Administrador'),
        ('secretario', 'Secretário'),
        ('professor', 'Professor'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='professor')
    photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Garante que cada User tenha um Profile único"""
    if created:
        # 🔒 Cria somente se não existir
        Profile.objects.get_or_create(user=instance)


# =============================
# Cancelamento individual
# =============================
class ReservationException(models.Model):
    reservation = models.ForeignKey("Reservation", on_delete=models.CASCADE, related_name="exceptions")
    date = models.DateField()

    def __str__(self):
        return f"Exceção: {self.reservation} em {self.date}"


# =============================
# Reservas Avulsas (com recorrência)
# =============================
class Reservation(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    start_dt = models.DateTimeField()
    end_dt = models.DateTimeField()

    recurrence_rule = models.TextField(blank=True, null=True)
    is_cancelled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.room} - {self.user} ({self.start_dt})"

    def occurrences_between(self, start_range, end_range):
        """Retorna as ocorrências entre datas, respeitando cancelamentos e recorrências"""
        def aw(dt):
            return make_aware(dt) if is_naive(dt) else dt

        start_range = aw(start_range)
        end_range = aw(end_range)

        if self.is_cancelled:
            return []

        base_start = aw(self.start_dt)
        base_end = aw(self.end_dt)
        delta = base_end - base_start
        cancelled_dates = set(self.exceptions.values_list('date', flat=True))
        results = []

        # Sem recorrência
        if not self.recurrence_rule:
            if not (base_end <= start_range or base_start >= end_range):
                if base_start.date() not in cancelled_dates:
                    results.append((base_start, base_end, self))
            return results

        # Com recorrência
        try:
            rule = rrulestr(self.recurrence_rule, dtstart=base_start)
        except Exception:
            if base_start.date() not in cancelled_dates:
                results.append((base_start, base_end, self))
            return results

        window_start = start_range - timedelta(hours=1)
        window_end = end_range + timedelta(hours=1)

        for dt_start in rule.between(window_start, window_end, inc=True):
            dt_start = aw(dt_start)
            dt_end = dt_start + delta
            if dt_start.date() not in cancelled_dates:
                results.append((dt_start, dt_end, self))

        return results


# =============================
# Grade Fixa de Aulas
# =============================
WEEKDAY_CHOICES = [
    (0, 'Segunda'),
    (1, 'Terça'),
    (2, 'Quarta'),
    (3, 'Quinta'),
    (4, 'Sexta'),
    (5, 'Sábado'),
    (6, 'Domingo'),
]


class ScheduledClass(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='scheduled_classes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='scheduled_classes')
    title = models.CharField(max_length=100, blank=True, default='Aula')
    weekday = models.IntegerField(choices=WEEKDAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['room', 'weekday', 'start_time']

    def __str__(self):
        return f"{self.title} - {self.room.name} ({self.get_weekday_display()})"

    def duration(self):
        dt1 = datetime.combine(datetime.today(), self.start_time)
        dt2 = datetime.combine(datetime.today(), self.end_time)
        return int((dt2 - dt1).total_seconds() // 60)

    def occurrences_between(self, start_range, end_range):
        """Gera as aulas semanais no calendário FullCalendar"""
        if not self.is_active:
            return []

        def aw(dt):
            return make_aware(dt) if is_naive(dt) else dt

        start_range = aw(start_range)
        end_range = aw(end_range)

        current = start_range
        results = []

        # Encontrar o dia da semana correto
        days_to_wd = (self.weekday - current.weekday()) % 7
        current = (current + timedelta(days=days_to_wd)).replace(hour=0, minute=0, second=0, microsecond=0)

        while current < end_range:
            dt_start = current.replace(hour=self.start_time.hour, minute=self.start_time.minute)
            dt_end = current.replace(hour=self.end_time.hour, minute=self.end_time.minute)

            if not (dt_end <= start_range or dt_start >= end_range):
                results.append((dt_start, dt_end, self))

            current += timedelta(days=7)

        return results


# =============================
# Avisos (painel de administração)
# =============================
class Notice(models.Model):
    TIPO_CHOICES = [
        ('texto', 'Texto'),
        ('imagem', 'Imagem'),
    ]

    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='texto')
    titulo = models.CharField(max_length=200)
    corpo = models.TextField(blank=True, null=True)
    imagem = models.ImageField(upload_to='avisos/', blank=True, null=True)
    criado_em = models.DateTimeField(default=timezone.now)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-criado_em']

    def __str__(self):
        return f"{self.titulo} ({'ativo' if self.is_active else 'inativo'})"
