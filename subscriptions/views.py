import stripe
import json
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.contrib.auth.models import User

from subscriptions.utils import assign_credits_by_price_id, check_and_expire_subscription
from .models import StripeCustomer, UserSubscription
from django.utils import timezone
from datetime import datetime
from django.contrib.auth.decorators import login_required
from django.contrib import messages




stripe.api_key = settings.STRIPE_SECRET_KEY
PRICES = {
    '5': 'price_1RS9OPSEv1tl6ISPdv59qBYR',
    '10': 'price_1RS9PpSEv1tl6ISPgkliHHER',
    '20': 'price_1RS9Q6SEv1tl6ISP6u6RFTWx',
}

def subscribe_view(request):
    try:
        current_plan = UserSubscription.objects.get(user=request.user, is_active=True).plan
    except UserSubscription.DoesNotExist:
        current_plan = None

    return render(request, 'subscription.html', {
        'current_plan': current_plan,
        'prices': PRICES  # Optional, if you want to iterate over them dynamically
    })
    
    # return render(request, "subscription.html", {
    #     "STRIPE_PUBLIC_KEY": settings.STRIPE_PUBLIC_KEY
    # })

def subscription_success(request):
    session_id = request.GET.get("session_id")
    session = stripe.checkout.Session.retrieve(session_id) if session_id else None
    return render(request, "subscription_success.html", {
        "session": session
    })

def subscription_cancel(request):
    return render(request, "subscription_cancel.html")


@login_required
@require_POST
def create_checkout_session(request):
    user = request.user
    price_id = request.POST.get("price_id")

    if not price_id:
        return HttpResponse("Missing price_id", status=400)
    # ✅ Check if price_id is one of the allowed values
    if price_id not in PRICES.values():
        return HttpResponse("Invalid price ID", status=400)
    
    try:
        user_sub = UserSubscription.objects.get(user=user)
        if user_sub.is_active:

                        # Retrieve the subscription to get the subscription item ID

            stripe.Subscription.cancel(user_sub.stripe_subscription_id)
            # subscription = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
            # item_id = subscription["items"]["data"][0]["id"]
            # ✅ Change the plan on the existing Stripe subscription
            # stripe.Subscription.modify(
            #     user_sub.stripe_subscription_id,
            #     cancel_at_period_end=False,
            #     # proration_behavior='none',
            #     # billing_cycle_anchor='now',
            #     # items=[{
            #     #     'id': item_id,
            #     #     'price': price_id,
            #     # }]
            # )
            #stripe.Subscription.delete(user_sub.stripe_subscription_id)
            # Update our model (will also get updated by webhook)
            # user_sub.plan = price_id
            # user_sub.save()

            # messages.success(request, "Your plan has been updated successfully.")
            # return redirect('subscription-success')  # Optional: change destination
    except UserSubscription.DoesNotExist:
        pass  # No subscription exists yet; continue below


    checkout_session = stripe.checkout.Session.create(
        customer_email=request.user.email,
        payment_method_types=['card'],
        line_items=[{
            'price': price_id,
            'quantity': 1,
        }],
        mode='subscription',
        success_url='https://stripe.countnine.com/success/?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=request.build_absolute_uri('/cancel/') + '?session_id={CHECKOUT_SESSION_ID}',
        metadata={"user_id": request.user.id}
    )
    return redirect(checkout_session.url)


@require_POST
@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET
    event = None

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    # if event['type'] == 'checkout.session.completed':
    #     session = event['data']['object']
    #     customer_id = session.get("customer")
    #     email = session.get("customer_email")
    #     user = User.objects.filter(email=email).first()
    #     if user:
    #         StripeCustomer.objects.update_or_create(
    #             user=user,
    #             defaults={"stripe_customer_id": customer_id, "is_active": True}
    #         )
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer = stripe.Customer.retrieve(session["customer"])
        subscription = stripe.Subscription.retrieve(session["subscription"])
        print(subscription)

        from django.contrib.auth.models import User
        user = User.objects.get(id=session["metadata"]["user_id"])

        UserSubscription.objects.update_or_create(
            user=user,
            defaults={
                'stripe_customer_id': session["customer"],
                'stripe_subscription_id': session["subscription"],
                'plan': subscription["items"]["data"][0]["price"]["id"],
                'current_period_end': datetime.fromtimestamp(subscription["items"]["data"][0]["current_period_end"]),
                'is_active': True  # ✅ set active
            }
        )

    elif event['type'] == 'invoice.payment_succeeded':

        sub_data = event['data']['object']
        #customer_id = sub_data['customer']
        #subscription_id = sub_data['subscription']

        subscription = event['data']['object']['customer']
        #subscription = stripe.Subscription.retrieve(subscription_id)
        user_sub = UserSubscription.objects.get(stripe_customer_id=subscription)
        # ✅ Ensure is_active is True on payment
        user_sub.is_active = True
        user_sub.current_period_end = datetime.fromtimestamp(subscription["items"]["data"][0]["current_period_end"])
        user_sub.save()
        # ➕ Call your credit increment function here
        assign_credits_by_price_id(user_sub, user_sub.plan)

    elif event['type'] == 'invoice.payment_failed':
        sub_data = event['data']['object']
        customer_id = sub_data['customer']
        try:
            user_sub = UserSubscription.objects.get(stripe_customer_id=customer_id)
            user_sub.credits = 0  # 🔻 Revoke credits
            user_sub.is_active = False  # Optional: mark as inactive
            user_sub.save()
        except UserSubscription.DoesNotExist:
            pass

    elif event['type'] == 'customer.subscription.updated':
        sub_data = event['data']['object']
        try:
            user_sub = UserSubscription.objects.get(stripe_subscription_id=sub_data['id'])
            user_sub.plan = sub_data['items']['data'][0]['price']['id']
            user_sub.current_period_end = datetime.fromtimestamp(sub_data['current_period_end'])
            user_sub.is_active = sub_data['status'] == 'active'  # ✅ update is_active accordingly
            user_sub.save()
        except UserSubscription.DoesNotExist:
            pass

    elif event['type'] == 'customer.subscription.deleted':
        try:
            sub_data = event['data']['object']
            user_sub = UserSubscription.objects.get(stripe_subscription_id=sub_data['id'])
            user_sub.is_active = False  # ✅ deactivate
            user_sub.credits = 0  # 🔻 Clear credits on deletion
            user_sub.save()
        except UserSubscription.DoesNotExist:
            pass

    return HttpResponse(status=200)


from django.contrib.auth.decorators import login_required
# Create your views here.
@login_required
def home(request):
    user = request.user
    sub = user.usersubscription

    # ✅ Expire credits if subscription period has ended
    check_and_expire_subscription(sub)
    
    if request.method == 'POST':
        credits = int(request.POST.get('credits', 0))
        if not sub.is_active or sub.credits < credits:
            messages.error(request, "Not enough credits or subscription expired.")
            return redirect('dashboard')

        sub.credits = max(0, sub.credits - credits)
        sub.save()
        messages.success(request, f"Used {credits} credits.")
        return redirect('dashboard')

    return render(request,"dashboard.html")


def login(request):
    return render(request,"login.html")







@login_required
def pause_subscription(request):
    try:
        user_sub = request.user.usersubscription
        stripe.Subscription.modify(
            user_sub.stripe_subscription_id,
            pause_collection={"behavior": "mark_uncollectible"}
        )
        user_sub.is_active = False
        user_sub.save()
        messages.success(request, "Subscription paused.")
    except Exception as e:
        messages.error(request, f"Error: {e}")
    return redirect('dashboard')


@login_required
def resume_subscription(request):
    try:
        user_sub = request.user.usersubscription
        stripe.Subscription.modify(
            user_sub.stripe_subscription_id,
            pause_collection=""  # Clear pause
        )
        # Fetch updated subscription period
        subscription = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        user_sub.current_period_end = datetime.fromtimestamp(subscription["current_period_end"])
        user_sub.is_active = True
        user_sub.save()
        messages.success(request, "Subscription resumed.")
    except Exception as e:
        messages.error(request, f"Error: {e}")
    return redirect('dashboard')








@login_required
def update_payment_method(request):
    user_sub = request.user.usersubscription
    session = stripe.checkout.Session.create(
        customer=user_sub.stripe_customer_id,
        payment_method_types=['card'],
        mode='setup',
        success_url=request.build_absolute_uri('/dashboard/'),
        cancel_url=request.build_absolute_uri('/dashboard/'),
    )
    return redirect(session.url)


