from _single_test_file_runner import run_all_tests_if_test_file_called

if __name__ == "__main__":
    run_all_tests_if_test_file_called()

from utils import req, dict_must_contain


def test_health():
    res = req("api/health")
    dict_must_contain(res, required_keys_and_val={"healthy": True})
