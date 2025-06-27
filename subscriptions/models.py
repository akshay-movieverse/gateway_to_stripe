from django.contrib.auth.models import User
from django.db import models
import stripe

class StripeCustomer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    credits = models.IntegerField(default=0)
    last_renewed = models.DateField(null=True, blank=True)

# Define choices for subscription status
SUBSCRIPTION_STATUS_CHOICES = (
    ('active', 'Active'),
    ('paused', 'Paused'),
    ('canceled', 'Canceled'),
    ('past_due', 'Past Due'),
    ('unpaid', 'Unpaid'),
    ('incomplete', 'Incomplete'),
    ('incomplete_expired', 'Incomplete Expired'),
    ('ended', 'Ended'), # Custom status for truly ended subscriptions
)

class StripePlan(models.Model):
    """
    Represents a subscription plan offered by the application,
    mapping to a Stripe Price ID and defining credit allocations.
    """
    name = models.CharField(max_length=100, help_text="e.g., 'Basic Plan', 'Pro Yearly'")
    stripe_price_id = models.CharField(max_length=255, unique=True, help_text="The Stripe Price ID for this plan.")
    plan_type = models.CharField(max_length=50, choices=[
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
        ('lifetime', 'Lifetime'),
    ], default='monthly', help_text="Type of subscription plan (monthly, yearly, or lifetime).")
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price of the plan in USD.")
    currency = models.CharField(max_length=10, default='usd', help_text="Currency for the plan price, e.g., 'usd'.")
    description = models.TextField(blank=True, help_text="A brief description of the plan's features.")
    monthly_credit_allotment = models.IntegerField(default=0,
                                                   help_text="Credits to assign monthly for this plan.")
    is_active = models.BooleanField(default=True, help_text="Whether this plan is currently offered.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} (Stripe Price ID: {self.stripe_price_id})"
    


class UserSubscription(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255)
    stripe_subscription_id = models.CharField(max_length=255)
    plan = models.ForeignKey(StripePlan, on_delete=models.SET_NULL, null=True, blank=True, help_text="The active plan the user is subscribed to.")
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    is_active = models.BooleanField(default=True)  # ✅ Add this field
    is_paused = models.BooleanField(default=False)
    credits = models.IntegerField(default=0)
    
    status = models.CharField(max_length=20, choices=SUBSCRIPTION_STATUS_CHOICES, default='incomplete',
                              help_text="Current status of the Stripe subscription.")
    
    last_credit_refill_date = models.DateTimeField(null=True, blank=True,
                                                    help_text="The last date credits were refilled for the user.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan} - Active: {self.is_active}"
    
    # def get_subscription_item_id(self):
    #     """Helper method to get the ID of the first subscription item."""
    #     try:
    #         subscription = stripe.Subscription.retrieve(self.stripe_subscription_id)
    #         return subscription['items']['data'][0].id
    #     except Exception:
    #         return None



class Invoice(models.Model):
    """
    Records historical invoice data from Stripe for auditing and user display.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')

    stripe_invoice_id = models.CharField(max_length=255, unique=True, help_text="The unique ID of the invoice in Stripe.")
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, help_text="The total amount due for this invoice.")
    currency = models.CharField(max_length=3, default='usd', help_text="e.g., 'usd', 'eur'")
    status = models.CharField(max_length=20, help_text="Status of the invoice (e.g., 'paid', 'open', 'void').")
    invoice_pdf = models.URLField(max_length=500, blank=True, null=True, help_text="URL to the invoice PDF hosted by Stripe.")
    invoice_page = models.URLField(max_length=500, blank=True, null=True, help_text="URL to the invoice page on Stripe's dashboard.")
    period_start = models.DateTimeField(help_text="The start of the billing period covered by this invoice.")
    period_end = models.DateTimeField(help_text="The end of the billing period covered by this invoice.")
    is_successful_payment = models.BooleanField(default=False, help_text="True if the payment for this invoice was successful.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Invoices"

    def __str__(self):
        return f"Invoice {self.stripe_invoice_id} for {self.user.username} - Amount: {self.amount_due} {self.currency}"
