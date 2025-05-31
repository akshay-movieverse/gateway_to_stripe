import stripe
import json
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.contrib.auth.models import User
from .models import StripeCustomer

stripe.api_key = settings.STRIPE_SECRET_KEY

def subscribe_view(request):
    return render(request, "subscription.html", {
        "STRIPE_PUBLIC_KEY": settings.STRIPE_PUBLIC_KEY
    })

def subscription_success(request):
    return render(request, "subscription_success.html")

def subscription_cancel(request):
    return render(request, "subscription_cancel.html")

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_id = session.get("customer")
        email = session.get("customer_email")
        user = User.objects.filter(email=email).first()
        if user:
            StripeCustomer.objects.update_or_create(
                user=user,
                defaults={"stripe_customer_id": customer_id, "is_active": True}
            )

    elif event['type'] == 'invoice.paid':
        invoice = event['data']['object']
        customer_id = invoice['customer']
        stripe_customer = StripeCustomer.objects.filter(stripe_customer_id=customer_id).first()
        if stripe_customer:
            stripe_customer.credits = 100
            stripe_customer.is_active = True
            stripe_customer.last_renewed = timezone.now().date()
            stripe_customer.save()

    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        customer_id = subscription['customer']
        stripe_customer = StripeCustomer.objects.filter(stripe_customer_id=customer_id).first()
        if stripe_customer:
            stripe_customer.is_active = False
            stripe_customer.save()

    return HttpResponse(status=200)


from django.contrib.auth.decorators import login_required
# Create your views here.
@login_required
def home(request):
    return render(request,"dashboard.html")


def login(request):
    return render(request,"login.html")


