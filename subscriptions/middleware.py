# subscriptions/middleware.py

from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import UserSubscription, StripePlan
from .utils import (
    handle_subscription_period_end,
    check_and_refill_monthly_credits,
)

class CreditRefillMiddleware:
    """
    On each request:
      1. Expire subscriptions whose period has ended.
      2. Refill monthly credits if a new month has begun.
      3. If no active subscription, ensure user is on the 'lifetime' plan.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            try:
                sub = UserSubscription.objects.select_related('plan').get(user=user)
            except UserSubscription.DoesNotExist:
                # If no subscription record at all, you might create one here,
                # or rely on your post_save signal for User→lifetime-plan.
                sub = None

            if sub and sub.plan.plan_type in ('monthly', 'yearly'):
                # 1) Expire & cleanup if period ended:
                handle_subscription_period_end(sub)

                # 2) If still active and not lifetime, refill monthly credits:
                if sub.is_active and sub.plan.plan_type in ('monthly', 'yearly'):
                    check_and_refill_monthly_credits(sub)

                # 3) If not active (or canceled), switch to lifetime plan:
                # if not sub.is_active or sub.plan.plan_type == 'lifetime':
                #     lifetime_plan = get_object_or_404(StripePlan, plan_type='lifetime')
                #     sub.plan = lifetime_plan
                #     # Only grant the one‐time credits if they don’t already have them:
                #     if sub.credits == 0:
                #         sub.credits = lifetime_plan.monthly_credit_allotment  # (3 in your case)
                #     sub.is_active = False
                #     sub.status = 'ended'
                #     sub.stripe_subscription_id = None
                #     sub.save()
            # end if sub

        response = self.get_response(request)
        return response
