from django.contrib import admin
from .models import Room, Profile, Reservation, ReservationException

admin.site.register(Room)
admin.site.register(Profile)
admin.site.register(Reservation)
admin.site.register(ReservationException)
