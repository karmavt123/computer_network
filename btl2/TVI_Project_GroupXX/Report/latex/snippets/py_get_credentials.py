def get_credentials():
    user = os.environ.get("TVI_SSH_USER") or input("Enter SSH Username: ")
    password = os.environ.get("TVI_SSH_PASS") or getpass("Enter SSH Password: ")
    secret = os.environ.get("TVI_ENABLE_PASS", password)
    return user, password, secret
