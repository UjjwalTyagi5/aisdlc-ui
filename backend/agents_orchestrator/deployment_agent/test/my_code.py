
USER_DB = {
    "user1": "pass123",
    "user2": "password"
}

def login(username: str, password: str) -> dict:
    """
    Simulate login functionality.

    Returns a dict with success status and message or error.
    """
    if not username:
        return {"success": False, "error": "Username is required"}

    if not password:
        return {"success": False, "error": "Password is required"}

    if username not in USER_DB or USER_DB[username] != password:
        return {"success": False, "error": "Invalid credentials"}

    return {"success": True, "message": "User is redirected to dashboard"}

def forgot_password() -> str:
    """
    Simulate clicking 'Forgot Password' link.
    """
    return "Redirected to password recovery page"
