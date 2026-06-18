from utils import req, dict_must_contain

def test_health():
    res = req("api/health")
    dict_must_contain(res, required_keys_and_val={"healthy": True})
