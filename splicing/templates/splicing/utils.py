from accounts.models import UserProfile


def is_contractor(user):
    """
    Checks if a given user is authenticated and has a UserProfile
    with a contractor company name set.
    """
    if not user.is_authenticated:
        return False

    try:
        # Uses the confirmed correct related_name: .profile
        if user.profile and user.profile.contractor_company_name:
            return True
        return False
    except AttributeError:
        # Catches if the profile itself is missing
        return False