PRICES = {
    '5': 'price_1RS9OPSEv1tl6ISPdv59qBYR',
    '10': 'price_1RS9PpSEv1tl6ISPgkliHHER',
    '20': 'price_1RS9Q6SEv1tl6ISP6u6RFTWx',
}


def assign_credits_by_price_id(user_sub, price_id):
    price_to_credits = {
        PRICES['5']: 50,
        PRICES['10']: 100,
        PRICES['20']: 9999,
    }

    credits = price_to_credits.get(price_id)
    if credits is not None:
        user_sub.credits = credits
        user_sub.save()
    else:
        raise ValueError(f"Invalid price_id: {price_id}")
    

from django.utils import timezone

from subscriptions.models import UserSubscription

def check_and_expire_subscription(user_sub):
    if user_sub.current_period_end < timezone.now():
        user_sub.is_active = False
        user_sub.credits = 0
        user_sub.save()



def handle_subscription_period_end(user_sub: UserSubscription):
    """
    Checks if a user's subscription period has ended. If so, it deactivates
    the subscription and revokes all remaining credits.
    This function should be called on user access (e.g., dashboard view).
    """
    if user_sub.current_period_end and user_sub.current_period_end < timezone.now() and user_sub.is_active:
        user_sub.is_active = False
        user_sub.status = 'ended' # Custom status for internal tracking
        user_sub.credits = 0 # Revoke all credits
        user_sub.save()
        #logger.info(f"Subscription for {user_sub.user.username} has ended. Deactivated and credits revoked.")