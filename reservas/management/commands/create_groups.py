from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from reservas.models import Reservation

class Command(BaseCommand):
    help = 'Cria grupos: Professor, Secretario, Administrador'

    def handle(self, *args, **options):
        professor, _ = Group.objects.get_or_create(name='Professor')
        secretario, _ = Group.objects.get_or_create(name='Secretario')
        admin, _ = Group.objects.get_or_create(name='Administrador')

        ct = ContentType.objects.get_for_model(Reservation)
        add_perm = Permission.objects.get(codename='add_reservation', content_type=ct)
        change_perm = Permission.objects.get(codename='change_reservation', content_type=ct)
        delete_perm = Permission.objects.get(codename='delete_reservation', content_type=ct)

        professor.permissions.clear()
        secretario.permissions.set([add_perm, change_perm])
        admin.permissions.set([add_perm, change_perm, delete_perm])

        self.stdout.write(self.style.SUCCESS('Grupos criados/atualizados.'))
