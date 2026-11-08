import argparse
import getpass

from . import auth, service


def main():
    parser = argparse.ArgumentParser(description="Provision or rotate a DealPilot bank employee password")
    parser.add_argument("email", help="Existing seeded bank employee email")
    args = parser.parse_args()
    service.initialize()
    auth.seed_bank_users()
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    auth.set_bank_password(args.email, password)
    print("Bank employee password updated. The password was not displayed or logged.")


if __name__ == "__main__":
    main()
