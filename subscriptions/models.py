from django.contrib.auth.models import User
from django.db import models
import stripe

class StripeCustomer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    credits = models.IntegerField(default=0)
    last_renewed = models.DateField(null=True, blank=True)


class UserSubscription(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255)
    stripe_subscription_id = models.CharField(max_length=255)
    plan = models.CharField(max_length=100)
    current_period_end = models.DateTimeField()
    is_active = models.BooleanField(default=True)  # ✅ Add this field

    def __str__(self):
        return f"{self.user.username} - {self.plan} - Active: {self.is_active}"
    
    def get_subscription_item_id(self):
        """Helper method to get the ID of the first subscription item."""
        try:
            subscription = stripe.Subscription.retrieve(self.stripe_subscription_id)
            return subscription['items']['data'][0].id
        except Exception:
            return None