from .views import is_staff_like

def user_permissions(request):
    if request.user.is_authenticated:
        return {
            'is_staff_like': is_staff_like(request.user)
        }
    return {'is_staff_like': False}
