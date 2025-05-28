# https://oidc-provider-mock.readthedocs.io/latest/
from typing import List, Dict
import oidc_provider_mock
import logging
import time
import requests
from pathlib import Path

import yaml


def provision_test_users(oidc_mockup_server_base_url: str):
    users_database_file = Path(Path(__file__).parent, "testusers.yaml")
    users: List[Dict] = []
    if users_database_file.is_file():
        with open(users_database_file, "r") as file:
            file_content_parsed = yaml.safe_load(file.read())
            if "Users" in file_content_parsed:
                users.extend(file_content_parsed["Users"])
    for user in users:
        print(f"Create testuser data: {user}")
        res = requests.put(
            f"{oidc_mockup_server_base_url}/users/{user['sub']}",
            json=user,
        )
        res.raise_for_status()


def start_oidc_server_thread(port: int = 8884):
    with oidc_provider_mock.run_server_in_thread(port=port) as server:
        server_url = f"http://localhost:{server.server_port}"
        print(f"OIDC Provider Mockup Server listening at {server_url}")
        provision_test_users(server_url)
        try:
            if __name__ == "__main__":
                print("Press Ctrl+C to exit the loop.")

            while True:
                pass
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt detected. Exiting the loop safely.")


if __name__ == "__main__":
    start_oidc_server_thread()
