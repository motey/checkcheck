from utils import req


def run_tests():
    res = req("user/me")
    # print(res)


def test_health():
    req("health/")
