"""Personal-data helpers shared by every view that is not the data's owner."""


def mask_email(email: str) -> str:
    """`leonardo@gmail.com` -> `l***@gmail.com`; keeps the domain, hides the rest.

    A value without a usable local part or domain is fully hidden rather than
    echoed back, so the helper can never leak a malformed address.
    """
    local, sep, domain = email.strip().rpartition("@")
    if not sep or not domain:
        return "***"
    return f"{local[:1]}***@{domain}"
