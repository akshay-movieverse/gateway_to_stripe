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
    

from datetime import timedelta
from django.utils import timezone

from subscriptions.models import StripePlan, UserSubscription

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




def check_and_refill_monthly_credits(user_sub: UserSubscription):
    """
    Checks if a user's subscription is due for a monthly credit refill
    and adds credits accordingly. This function handles multiple missed refills.
    It relies on the `monthly_credit_allotment` and `last_credit_refill_date`
    fields on the UserSubscription model.
    """
    # Only refill if subscription is active and has an allotment
    if not user_sub.is_active :
        #logger.debug(f"Skipping monthly credit refill for {user_sub.user.username} (not active or no allotment).")
        return

    now = timezone.now()
    refill_start_point = user_sub.last_credit_refill_date #or user_sub.current_period_start

    if not refill_start_point:
        # logger.warning(f"User {user_sub.user.username} has no start date or last refill date. "
        #                f"Cannot perform monthly credit refill check.")
        return

    # Adjust refill_start_point to the beginning of the next expected refill period
    # If last refill was on Jan 15, next refill is Feb 15. If now is Feb 16, credits due.
    next_expected_refill = refill_start_point + timedelta(days=30) # Simple approximation of a month

    credits_refilled_this_run = False
    
    # Loop to add credits for all missed months until 'now' or 'current_period_end'
    # Ensure we don't refill past the end of the current Stripe billing period
    while next_expected_refill <= now and (next_expected_refill <= user_sub.current_period_end) :
        
        user_sub.credits = user_sub.plan.monthly_credit_allotment
        # logger.info(f"Refilled {user_sub.monthly_credit_allotment} credits for {user_sub.user.username}. "
        #             f"New total: {user_sub.credits}")
        
        # Advance the next expected refill date by one month
        next_expected_refill += timedelta(days=30)
        credits_refilled_this_run = True

    if credits_refilled_this_run:
        # Update last_credit_refill_date to the last date credits were actually considered for refill.
        # This prevents re-adding credits for the same period.
        user_sub.last_credit_refill_date = next_expected_refill - timedelta(days=30) # Set to the point of last successful refill
        user_sub.save()
        # logger.info(f"Monthly credit refill complete for {user_sub.user.username}. "
        #             f"New last_credit_refill_date: {user_sub.last_credit_refill_date}")
    else:
        pass
        #logger.debug(f"No monthly credits to refill for {user_sub.user.username} at this time.")





def assign_credits_based_on_plan(user_sub: UserSubscription, stripe_plan: StripePlan):
    """
    Assigns initial credits to a user's subscription based on the associated Plan's
    monthly credit allotment. This is typically called on initial subscription
    or yearly payment success.
    """
    # try:
    #plan = Plan.objects.get(stripe_price_id=stripe_price_id)
    #user_sub.monthly_credit_allotment = plan.monthly_credit_allotment
    user_sub.credits = stripe_plan.monthly_credit_allotment # Assign initial monthly allotment
    #user_sub.last_credit_refill_date = timezone.now() # Mark as refilled now
    user_sub.save()
    #     logger.info(f"Assigned initial {plan.monthly_credit_allotment} credits to {user_sub.user.username} "
    #                 f"for plan {plan.name}.")
    # except Plan.DoesNotExist:
    #     logger.error(f"Plan with price ID {stripe_price_id} not found. Cannot assign credits to {user_sub.user.username}.")
    #     # Optionally, assign a default credit or raise an exception
    #     user_sub.monthly_credit_allotment = 0
    #     user_sub.credits = 0
    #     user_sub.save()
    # except Exception as e:
    #     logger.error(f"Error assigning credits to {user_sub.user.username}: {e}", exc_info=True)