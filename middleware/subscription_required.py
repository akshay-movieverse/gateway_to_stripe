from django.shortcuts import redirect
from django.urls import reverse
from subscriptions.models import StripeCustomer

class SubscriptionRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            allowed_paths = [
                reverse('subscription'),
                reverse('subscription-success'),
                reverse('subscription-cancel'),
                #reverse('logout'),
                '/admin/',
            ]
            if not any(request.path.startswith(path) for path in allowed_paths):
                try:
                    sub = StripeCustomer.objects.get(user=request.user)
                    if not sub.is_active:
                        return redirect('subscription')
                except StripeCustomer.DoesNotExist:
                    return redirect('subscription')
        return self.get_response(request)
