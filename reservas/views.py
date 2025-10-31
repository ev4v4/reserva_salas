from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.dateparse import parse_datetime, parse_date
from django.views.decorators.http import require_POST
from django.utils.timezone import make_aware, is_naive, now, get_current_timezone
from datetime import datetime, timedelta, time
from django.contrib import messages
from .models import Notice, Profile
from django.db import models
from .forms import ProfilePhotoForm
import json

from .models import (
    Room, Reservation, ReservationException,
    ScheduledClass, WEEKDAY_CHOICES
)

WEEKDAY_LABELS = {
    0: "Segunda", 1: "Terça", 2: "Quarta", 3: "Quinta",
    4: "Sexta", 5: "Sábado", 6: "Domingo"
}

def _t2m(t: time) -> int:
    return t.hour * 60 + t.minute

def _m2t(m: int) -> time:
    m = max(0, min(m, 23*60+59))
    return time(hour=m // 60, minute=m % 60)

def _overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    # Sobrepõe se A começa antes de B terminar E A termina depois de B começar
    return (_t2m(a_start) < _t2m(b_end)) and (_t2m(a_end) > _t2m(b_start))

def _has_conflict(room, weekday: int, start_t: time, end_t: time, exclude_id: int|None=None) -> bool:
    print("\n🔎 Verificando conflito:")
    print(f"➡ Sala: {room.slug}, Dia: {weekday}, Início: {start_t}, Fim: {end_t}")

    # 1️⃣ Conflito com Aulas Fixas
    sc_qs = ScheduledClass.objects.filter(room=room, weekday=weekday, is_active=True)
    if exclude_id:
        sc_qs = sc_qs.exclude(id=exclude_id)

    for sc in sc_qs:
        if _overlap(start_t, end_t, sc.start_time, sc.end_time):
            print(f"🚫 Conflito aula fixa: {sc.id} {sc.title} {sc.start_time}-{sc.end_time}")
            return True

    # 2️⃣ Conflito com Reservas (baseado em ocorrências reais)
    today = now().date()
    days_ahead = (weekday - today.weekday()) % 7
    check_date = today + timedelta(days=days_ahead)
    tz = get_current_timezone()

    day_start = make_aware(datetime.combine(check_date, start_t), timezone=tz)
    day_end = make_aware(datetime.combine(check_date, end_t), timezone=tz)

    reservations = Reservation.objects.filter(room=room, is_cancelled=False)

    for r in reservations:
        for s, e, _ in r.occurrences_between(day_start - timedelta(hours=1), day_end + timedelta(hours=1)):
            if not (day_end <= s or day_start >= e):
                print(f"🚫 Conflito reserva: {r.id} {s.time()}-{e.time()} do user {r.user}")
                return True

    print("✅ Nenhum conflito encontrado")
    return False

def _suggest_alternatives(room, weekday: int, around_start: time, duration_min: int, exclude_id: int|None=None, max_suggestions: int=8):
    """
    Sugere horários livres no mesmo dia/sala.
    Varre o dia de 06:00 a 23:00 em passos de 30 min e escolhe os mais próximos.
    """
    step = 30
    start_window = _t2m(time(6, 0))
    end_window   = _t2m(time(23, 0))
    want = _t2m(around_start)
    dur = duration_min

    # Colete os intervalos ocupados
    busy = []
    qs = ScheduledClass.objects.filter(room=room, weekday=weekday, is_active=True)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    for sc in qs:
        busy.append((_t2m(sc.start_time), _t2m(sc.end_time)))

    # Varre slots
    candidates = []
    for start_m in range(start_window, end_window+1, step):
        end_m = start_m + dur
        if end_m > (23*60+59):
            continue
        # checa conflito com qualquer busy
        if any(not (end_m <= b0 or start_m >= b1) for (b0, b1) in busy):
            continue
        candidates.append(start_m)

    # Ordena por proximidade ao horário pedido
    candidates.sort(key=lambda m: abs(m - want))

    # Formata sugestões
    result = []
    for m in candidates[:max_suggestions]:
        t = _m2t(m)
        label = f"{WEEKDAY_LABELS.get(weekday, 'Dia')} • {t.strftime('%H:%M')}"
        result.append({
            "weekday": weekday,
            "weekday_label": WEEKDAY_LABELS.get(weekday, "Dia"),
            "start": t.strftime("%H:%M"),
            "label": label
        })
    return result

def _is_ajax(request) -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

# =============================
# Helper: perfil administrativo
# =============================
def is_staff_like(user):
    return (
        user.is_superuser
        or user.groups.filter(name__iexact='Secretario').exists()
        or user.groups.filter(name__iexact='Administrador').exists()
    )

# =============================
# Home — Calendário (cliente)
# =============================
@login_required
def home(request):
    rooms = Room.objects.all().order_by('id')
    teachers = None
    if is_staff_like(request.user):
        teachers = User.objects.filter(groups__name__iexact='Professor')

    from .models import Notice
    avisos = Notice.objects.filter(is_active=True).order_by('-criado_em')[:3]

    return render(request, 'reservas/calendar.html', {
        'rooms': rooms,
        'users': teachers,
        'is_staff_like': is_staff_like(request.user),
        'avisos': avisos,
    })


# =============================
# API Normal — Eventos (FullCalendar do cliente)
# =============================
@login_required
def events_feed(request):
    start = parse_datetime(request.GET.get('start'))
    end = parse_datetime(request.GET.get('end'))
    room_slug = request.GET.get('room')

    if not (start and end):
        return JsonResponse([], safe=False)

    events = []

    # --- Reservas normais ---
    res_qs = Reservation.objects.filter(is_cancelled=False)
    if room_slug:
        res_qs = res_qs.filter(room__slug=room_slug)

    # Você pode ajustar as cores por sala se quiser
    for r in res_qs.select_related('room', 'user'):
        teacher = r.user.get_full_name() or r.user.username
        for s, e, _ in r.occurrences_between(start, end):
            events.append({
                "id": f"r-{r.id}",
                "title": teacher.split()[0],  # no cliente, título curtinho
                "start": s.isoformat(),
                "end": e.isoformat(),
                "room_slug": r.room.slug,
                "backgroundColor": "#0BAFEE",
                "textColor": "#ffffff",
                "extendedProps": {
                    "type": "reservation",
                    "teacher_name": teacher,   # <- disponível pra quem quiser mostrar
                    "room_name": r.room.name,
                    "owner_id": r.user.id,
                    "can_cancel": (r.user_id == request.user.id) or is_staff_like(request.user),
                }
            })

    # --- Aulas fixas (grade) ---
    sc_qs = ScheduledClass.objects.filter(is_active=True)
    if room_slug:
        sc_qs = sc_qs.filter(room__slug=room_slug)

    for sc in sc_qs.select_related('room', 'user'):
        teacher = sc.user.get_full_name() or sc.user.username
        title = f"{(sc.title or 'Aula').strip()} — {sc.user.get_full_name() or sc.user.username}"
        for s, e, _ in sc.occurrences_between(start, end):
            events.append({
                "id": f"sc-{sc.id}",
                "title": title,
                "start": s.isoformat(),
                "end": e.isoformat(),
                "room_slug": sc.room.slug,
                "backgroundColor": "#343a40",  # cor sólida p/ fixas
                "textColor": "#ffffff",
                "classNames": ["fixed-class", sc.room.slug],
                "extendedProps": {
                    "type": "scheduled_class",
                    "teacher_name": teacher,
                    "room_name": sc.room.name,
                }
            })

    return JsonResponse(events, safe=False)

# =============================
# Horários disponíveis (24h) — versão definitiva e compatível com Django 4.2+
# =============================
@login_required
@require_POST
def availability(request):
    from django.utils.timezone import localtime

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest("JSON inválido")

    date_str = data.get('date')
    room_slug = data.get('room_slug')
    duration_min = int(data.get('duration_min', 60))

    if not (date_str and room_slug):
        return HttpResponseBadRequest("Parâmetros faltando")

    target_date = parse_date(date_str)
    if not target_date:
        return HttpResponseBadRequest("Data inválida")

    # não permite datas passadas
    if target_date < now().date():
        return JsonResponse({'available': []})

    room = get_object_or_404(Room, slug=room_slug)

    tz = get_current_timezone()
    current_time = localtime(now())

    # janela completa do dia (00:00 → 23:59)
    day_start = make_aware(datetime.combine(target_date, time(0, 0)), timezone=tz)
    day_end = make_aware(datetime.combine(target_date, time(23, 59, 59)), timezone=tz)

    slot_len = timedelta(minutes=30)
    slots = []

    cur = day_start
    while cur + timedelta(minutes=duration_min) <= day_end:
        # se for hoje, pula apenas horários anteriores à hora atual (no mesmo fuso)
        if target_date == current_time.date() and cur.astimezone(tz).time() < current_time.time():
            cur += slot_len
            continue
        slots.append(cur)
        cur += slot_len

    # reservas existentes do dia
    existing = Reservation.objects.filter(
        room=room,
        start_dt__date=target_date,
        is_cancelled=False
    )

    busy_ranges = [(r.start_dt, r.end_dt) for r in existing]

    available_slots = []
    for start_time in slots:
        end_time = start_time + timedelta(minutes=duration_min)

        # verifica conflito com reservas
        conflict = any(
            s < end_time and start_time < e
            for s, e in busy_ranges
        )

        if not conflict:
            available_slots.append(start_time.astimezone(tz).strftime("%H:%M"))

    return JsonResponse({'available': available_slots})

# =============================
# Criar Reserva (cliente & staff)
# =============================
@login_required
@require_POST
def reserve_view(request):
    room_slug = request.POST.get('room_slug')
    date_str = request.POST.get('date')
    start_time_str = request.POST.get('start_time')
    duration_min = int(request.POST.get('duration_min', 60))
    recurrence_rule = (request.POST.get('recurrence_rule') or '').strip() or None

    if not (room_slug and date_str and start_time_str):
        return JsonResponse({'error': 'Dados incompletos'}, status=400)

    room = get_object_or_404(Room, slug=room_slug)

    # Agora sim podemos montar o datetime
    start_dt = make_aware(datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M"))
    end_dt = start_dt + timedelta(minutes=duration_min)

    if start_dt.date() < now().date():
        return JsonResponse({'error': 'Não é possível reservar no passado'}, status=400)

    # Quem está reservando?
    target_user = request.user
    if is_staff_like(request.user) and request.POST.get('user_id'):
        target_user = get_object_or_404(User, id=int(request.POST['user_id']))

    # ✅ CONFLITO com AULA FIXA AQUI!
    weekday = start_dt.weekday()
    if _has_conflict(room, weekday, start_dt.time(), end_dt.time()):
        return JsonResponse({'error': 'Conflito com uma aula fixa existente'}, status=409)

    # ✅ Conflito com outras reservas
    existing = Reservation.objects.filter(room=room, is_cancelled=False)
    for r in existing:
        for s, e, _ in r.occurrences_between(start_dt - timedelta(hours=1), end_dt + timedelta(hours=1)):
            if not (end_dt <= s or start_dt >= e):
                return JsonResponse({'error': 'Conflito com outra reserva'}, status=409)

    Reservation.objects.create(
        room=room,
        user=target_user,
        start_dt=start_dt,
        end_dt=end_dt,
        recurrence_rule=recurrence_rule
    )

    return JsonResponse({'ok': True, 'message': 'Reserva criada com sucesso!'})

# =============================
# Cancelar Reserva (single/all)
# =============================
@login_required
@require_POST
def cancel_reservation(request):
    sid = request.POST.get('reservation_id')
    mode = request.POST.get('mode', 'single')

    if not sid:
        return HttpResponseBadRequest("ID inválido")

    if str(sid).startswith('r-'):
        rid = int(str(sid)[2:])
    else:
        rid = int(str(sid))

    res = get_object_or_404(Reservation, id=rid)

    can_cancel = (
        res.user_id == request.user.id
        or is_staff_like(request.user)
    )
    if not can_cancel:
        return HttpResponseBadRequest("Sem permissão")

    if mode == 'all':
        res.is_cancelled = True
        res.save()
    else:
        # modo "single" = cancelar só esse dia
        date_str = request.POST.get('date')
        if not date_str:
            return HttpResponseBadRequest("Data obrigatória para cancelar só um dia")

        d = datetime.strptime(date_str, "%Y-%m-%d").date()

        # Se não há recorrência, simplesmente apaga a reserva
        if not res.recurrence_rule:
            res.is_cancelled = True
            res.save()
        else:
            # Para reservas recorrentes, salva exceção
            ReservationException.objects.get_or_create(reservation=res, date=d)

    return redirect('home')

# =============================
# Painel Administrativo — Agenda
# =============================
@user_passes_test(is_staff_like)
def admin_agenda(request):
    users = User.objects.all().order_by('first_name', 'last_name')
    rooms = Room.objects.all().order_by('id')
    return render(request, 'reservas/admin_agenda.html', {
        'users': users,
        'rooms': rooms
    })

# =============================
# API Admin — Eventos (inclui teacher_name)
# =============================
@user_passes_test(is_staff_like)
def admin_events_feed(request):
    room_slug = request.GET.get('room')
    user_filter = request.GET.get('user')

    start = datetime.fromisoformat(request.GET.get('start')).astimezone(get_current_timezone())
    end = datetime.fromisoformat(request.GET.get('end')).astimezone(get_current_timezone())

    events = []

    # Reservas normais
    res_qs = Reservation.objects.filter(is_cancelled=False)
    if room_slug:
        res_qs = res_qs.filter(room__slug=room_slug)
    if user_filter and user_filter != 'all':
        res_qs = res_qs.filter(user_id=user_filter)

    for r in res_qs.select_related('room', 'user'):
        teacher = r.user.get_full_name() or r.user.username
        for s, e, _ in r.occurrences_between(start, end):
            events.append({
                "id": f"r-{r.id}",
                "title": teacher,  # no admin pode usar nome completo
                "start": s.isoformat(),
                "end": e.isoformat(),
                "room_slug": r.room.slug,
                "backgroundColor": "#0BAFEE",
                "textColor": "#ffffff",
                "extendedProps": {
                    "type": "reservation",
                    "teacher_name": teacher,
                    "room_name": r.room.name,
                    "can_cancel": True
                }
            })

    # Aulas fixas (grade)
    sc_qs = ScheduledClass.objects.filter(is_active=True)
    if room_slug:
        sc_qs = sc_qs.filter(room__slug=room_slug)
    if user_filter and user_filter != 'all':
        sc_qs = sc_qs.filter(user_id=user_filter)

    for sc in sc_qs.select_related('room', 'user'):
        teacher = sc.user.get_full_name() or sc.user.username
        title = (sc.title or "Aula").strip() or "Aula"
        for s, e, _ in sc.occurrences_between(start, end):
            events.append({
                "id": f"sc-{sc.id}",
                "title": title,  # título da aula
                "start": s.isoformat(),
                "end": e.isoformat(),
                "room_slug": sc.room.slug,
                "backgroundColor": "#495057",  # cinza sólido
                "textColor": "#ffffff",
                "classNames": ["fixed-class", sc.room.slug],
                "extendedProps": {
                    "type": "scheduled_class",
                    "teacher_name": teacher,
                    "room_name": sc.room.name,
                    "can_cancel": True
                }
            })

    return JsonResponse(events, safe=False)

# =============================
# Cancelamento em lote (admin)
# =============================
@login_required
@require_POST
def cancel_bulk(request):
    if not is_staff_like(request.user):
        return HttpResponseBadRequest("Sem permissão")

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except:
        return HttpResponseBadRequest("Payload inválido")

    ids = payload.get('ids', [])
    if not ids:
        return HttpResponseBadRequest("Nenhum evento selecionado")

    for sid in ids:
        sid = str(sid)
        if sid.startswith('r-'):
            rid = int(sid[2:])
            res = Reservation.objects.filter(id=rid).first()
            if res:
                res.is_cancelled = True
                res.save()
        elif sid.startswith('sc-'):
            scid = int(sid[3:])
            sc = ScheduledClass.objects.filter(id=scid).first()
            if sc:
                sc.is_active = False
                sc.save()

    return JsonResponse({"ok": True})

# =============================
# Grade Fixa — telas (admin/secretário)
# =============================
@user_passes_test(is_staff_like)
def admin_grade_view(request):
    rooms = Room.objects.all().order_by('id')

    # 🔥 Corrigido: agora busca professores pelo role OU grupo
    teachers = User.objects.filter(
        models.Q(groups__name__iexact='Professor') |
        models.Q(profile__role__iexact='professor')
    ).distinct().order_by('first_name', 'last_name')

    classes = ScheduledClass.objects.select_related('room', 'user').order_by('room__id', 'weekday', 'start_time')

    return render(request, 'reservas/admin_grade.html', {
        'rooms': rooms,
        'users': teachers,
        'classes': classes,
        'WEEKDAY_CHOICES': WEEKDAY_CHOICES,
    })

@user_passes_test(is_staff_like)
@require_POST
def admin_grade_create(request):
    try:
        room = get_object_or_404(Room, slug=request.POST.get("room"))
        teacher = get_object_or_404(User, id=request.POST.get("user"))
        title = (request.POST.get("title") or "").strip() or "Aula"

        weekdays = request.POST.getlist("weekday")  # checkboxes
        if not weekdays:
            if _is_ajax(request):
                return JsonResponse({"ok": False, "error": "Selecione ao menos um dia."}, status=400)
            return HttpResponseBadRequest("Selecione ao menos um dia")

        h, m = map(int, request.POST.get("start").split(":"))
        start_time = time(hour=h, minute=m)
        end_dt = datetime(2000, 1, 1, h, m) + timedelta(minutes=int(request.POST.get("duration")))
        end_time = end_dt.time()

        # Checar conflitos por dia
        conflicts = []
        for wd_str in weekdays:
            wd = int(wd_str)
            if _has_conflict(room, wd, start_time, end_time):
                alts = _suggest_alternatives(room, wd, start_time, int(request.POST.get("duration")))
                conflicts.append({
                    "weekday": wd,
                    "weekday_label": WEEKDAY_LABELS.get(wd, "Dia"),
                    "start": start_time.strftime("%H:%M"),
                    "alternatives": alts
                })

        if conflicts:
            if _is_ajax(request):
                return JsonResponse({
                    "ok": False,
                    "error": "Conflito com outras aulas fixas.",
                    "conflicts": conflicts
                }, status=409)
            # Fluxo não-AJAX (fallback)
            return HttpResponseBadRequest("Conflito com outras aulas fixas.")

        # Sem conflitos → cria todas
        for wd_str in weekdays:
            wd = int(wd_str)
            ScheduledClass.objects.create(
                room=room,
                user=teacher,
                title=title,
                weekday=wd,
                start_time=start_time,
                end_time=end_time,
                is_active=True,
            )

        if _is_ajax(request):
            return JsonResponse({"ok": True, "message": "Aula(s) criada(s) com sucesso!"})
        return redirect("admin_grade")

    except Exception as e:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        return HttpResponseBadRequest(str(e))

@user_passes_test(is_staff_like)
@require_POST
def admin_grade_update(request):
    try:
        sc = get_object_or_404(ScheduledClass, id=int(request.POST.get("id")))
        sc.room = get_object_or_404(Room, slug=request.POST.get("room"))
        sc.user = get_object_or_404(User, id=request.POST.get("user"))
        sc.title = (request.POST.get("title") or sc.title).strip() or sc.title

        h, m = map(int, request.POST.get("start").split(":"))
        start_time = time(hour=h, minute=m)
        end_dt = datetime(2000, 1, 1, h, m) + timedelta(minutes=int(request.POST.get("duration")))
        end_time = end_dt.time()

        # weekday: pega 1º marcado se vier lista; senão mantém
        weekdays = request.POST.getlist("weekday")
        new_weekday = int(weekdays[0]) if weekdays else sc.weekday

        # Conflito (exclui a própria)
        if _has_conflict(sc.room, new_weekday, start_time, end_time, exclude_id=sc.id):
            alts = _suggest_alternatives(sc.room, new_weekday, start_time, int(request.POST.get("duration")), exclude_id=sc.id)
            if _is_ajax(request):
                return JsonResponse({
                    "ok": False,
                    "error": "Conflito com outra aula fixa.",
                    "conflicts": [{
                        "weekday": new_weekday,
                        "weekday_label": WEEKDAY_LABELS.get(new_weekday, "Dia"),
                        "start": start_time.strftime("%H:%M"),
                        "alternatives": alts
                    }]
                }, status=409)
            return HttpResponseBadRequest("Conflito com outra aula fixa.")

        # Salva
        sc.weekday = new_weekday
        sc.start_time = start_time
        sc.end_time = end_time
        sc.save()

        if _is_ajax(request):
            return JsonResponse({"ok": True, "message": "Aula atualizada com sucesso!"})
        return redirect("admin_grade")

    except Exception as e:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        return HttpResponseBadRequest(str(e))

@user_passes_test(is_staff_like)
@require_POST
def admin_grade_toggle(request):
    sc = get_object_or_404(ScheduledClass, id=int(request.POST.get("id")))
    sc.is_active = not sc.is_active
    sc.save()
    return redirect("admin_grade")

@user_passes_test(is_staff_like)
@require_POST
def admin_grade_delete(request):
    sc = get_object_or_404(ScheduledClass, id=int(request.POST.get("id")))
    sc.delete()
    return redirect("admin_grade")

# =============================
# Perfil do usuário
# =============================
@login_required
def profile_view(request):
    profile = request.user.profile

    # ✅ Atualização via AJAX (telefone)
    if request.method == 'POST' and request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest':
        new_phone = request.POST.get('phone', '').strip()
        if new_phone:
            profile.phone = new_phone
            profile.save(update_fields=['phone'])
            print(f"✅ Telefone salvo com sucesso: {new_phone}")
            return JsonResponse({'success': True, 'phone': new_phone})
        else:
            return JsonResponse({'success': False, 'error': 'Telefone inválido'}, status=400)

    # ✅ Remover foto
    elif request.method == 'POST' and 'remove_photo' in request.POST:
        if profile.photo:
            profile.photo.delete(save=True)
        profile.photo = None
        profile.save(update_fields=['photo'])
        return redirect('profile')

    # ✅ Upload de nova foto
    elif request.method == 'POST' and 'photo' in request.FILES:
        form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
        return redirect('profile')

    # ✅ GET padrão
    else:
        form = ProfilePhotoForm(instance=profile)

    return render(request, 'profile.html', {
        'form': form,
        'profile': profile
    })

# =============================
# Painel Administrativo — Usuários e Avisos
# =============================
@login_required
@user_passes_test(is_staff_like)
def admin_panel(request):
    """
    Painel para administradores e secretários:
    - Gerenciar usuários (criar/excluir)
    - Publicar e excluir avisos (texto ou imagem)
    """
    usuarios = Profile.objects.select_related('user').order_by('user__username')
    avisos = Notice.objects.filter(is_active=True).order_by('-criado_em')[:5]

    # ========================================
    # 👤 CRIAR NOVO USUÁRIO
    # ========================================
    if request.method == "POST":
    # Criar usuário (corrigido)
        if 'create_user' in request.POST:
            username = request.POST['username'].strip()
            email = request.POST['email'].strip()
            password = request.POST['password']
            role = request.POST['role']

            if not username or not password:
                messages.error(request, "Nome de usuário e senha são obrigatórios.")
                return redirect('admin_panel')

            if User.objects.filter(username=username).exists():
                messages.error(request, "Já existe um usuário com esse nome.")
                return redirect('admin_panel')

            # ✅ Criação do usuário base
            user = User.objects.create_user(username=username, email=email, password=password)

            # ✅ Nome e sobrenome
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            user.save(update_fields=['first_name', 'last_name'])

            # ✅ Perfil (role)
            if hasattr(user, "profile"):
                user.profile.role = role
                user.profile.save()
            else:
                # fallback se o sinal não tiver criado
                Profile.objects.create(user=user, role=role)

            # ✅ Sincroniza grupo automaticamente conforme o papel
            from django.contrib.auth.models import Group

            group_map = {
                'admin': 'Administrador',
                'secretario': 'Secretario',
                'professor': 'Professor',
            }

            group_name = group_map.get(role)
            if group_name:
                group, _ = Group.objects.get_or_create(name=group_name)
                user.groups.clear()  # remove qualquer grupo anterior
                user.groups.add(group)

            messages.success(request, f"Usuário '{user.get_full_name() or user.username}' criado com sucesso!")
            return redirect('admin_panel')


        # Excluir usuário (somente admin)
        elif 'delete_user' in request.POST:
            if request.user.profile.role != 'admin' and not request.user.is_superuser:
                messages.error(request, "Apenas administradores podem excluir usuários.")
                return redirect('admin_panel')

            user_id = request.POST.get('user_id')
            u = User.objects.filter(id=user_id).first()
            if u:
                u.delete()
                messages.success(request, "Usuário excluído com sucesso!")
            else:
                messages.error(request, "Usuário não encontrado.")
            return redirect('admin_panel')
        
        # Editar usuário (somente admin)
        elif 'edit_user' in request.POST:
            if request.user.profile.role != 'admin' and not request.user.is_superuser:
                messages.error(request, "Apenas administradores podem editar usuários.")
                return redirect('admin_panel')

            user_id = request.POST.get('user_id')
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            role = request.POST.get('role')

            u = User.objects.filter(id=user_id).first()
            if not u:
                messages.error(request, "Usuário não encontrado.")
                return redirect('admin_panel')

            # Atualiza campos
            u.first_name = first_name
            u.last_name = last_name
            u.email = email
            u.save(update_fields=['first_name', 'last_name', 'email'])

            # Atualiza papel (role)
            if hasattr(u, 'profile'):
                u.profile.role = role
                u.profile.save()

            # Atualiza grupo
            from django.contrib.auth.models import Group
            group_map = {
                'admin': 'Administrador',
                'secretario': 'Secretario',
                'professor': 'Professor',
            }
            group_name = group_map.get(role)
            if group_name:
                group, _ = Group.objects.get_or_create(name=group_name)
                u.groups.clear()
                u.groups.add(group)

            messages.success(request, f"Usuário '{u.get_full_name() or u.username}' atualizado com sucesso!")
            return redirect('admin_panel')

        # Criar aviso
        elif 'publicar_aviso' in request.POST:
            tipo = request.POST.get('tipo')
            titulo = request.POST.get('titulo')
            corpo = request.POST.get('corpo', '')
            imagem = request.FILES.get('imagem')

            if not titulo:
                messages.error(request, "O aviso precisa ter um título.")
                return redirect('admin_panel')

            Notice.objects.create(
                tipo=tipo,
                titulo=titulo,
                corpo=corpo if tipo == 'texto' else '',
                imagem=imagem if tipo == 'imagem' else None,
                criado_por=request.user
            )
            messages.success(request, "Aviso publicado com sucesso!")
            return redirect('admin_panel')

        # Excluir aviso (somente admin)
        elif 'delete_notice' in request.POST:
            if request.user.profile.role != 'admin' and not request.user.is_superuser:
                messages.error(request, "Apenas administradores podem excluir avisos.")
                return redirect('admin_panel')

            aviso_id = request.POST.get('aviso_id')
            aviso = Notice.objects.filter(id=aviso_id).first()
            if aviso:
                aviso.delete()
                messages.success(request, "Aviso excluído com sucesso!")
            else:
                messages.error(request, "Aviso não encontrado.")
            return redirect('admin_panel')

    # ========================================
    # GET padrão
    # ========================================
    return render(request, 'reservas/admin_panel.html', {
        'usuarios': usuarios,
        'avisos': avisos,
    })


# =============================
# Exibição dos avisos na tela inicial (home)
# =============================
@login_required
def home_with_notices(request):
    """
    Substitui a home atual se quiser mostrar os avisos abaixo do calendário.
    """
    rooms = Room.objects.all().order_by('id')
    teachers = None
    if is_staff_like(request.user):
        teachers = User.objects.filter(groups__name__iexact='Professor')

    avisos = Notice.objects.filter(is_active=True).order_by('-criado_em')[:3]

    return render(request, 'reservas/calendar.html', {
        'rooms': rooms,
        'users': teachers,
        'avisos': avisos,
        'is_staff_like': is_staff_like(request.user),
    })
