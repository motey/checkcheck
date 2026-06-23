import decimal
from utils import req, dict_must_contain

def test_checklist_move_under_and_above():
    """
    Create 3 checklists, verify their initial index order, then exercise
    move/under and move/above to confirm the index arithmetic is correct.
    """
    # Create 3 checklists in sequence; they get ascending indices.
    cl1 = req("api/checklist", "post", b={"name": "Move-CL1"})
    cl2 = req("api/checklist", "post", b={"name": "Move-CL2"})
    cl3 = req("api/checklist", "post", b={"name": "Move-CL3"})

    id1, id2, id3 = cl1["id"], cl2["id"], cl3["id"]

    def get_index(cl_id: str) -> decimal.Decimal:
        pos = req(f"api/checklist/{cl_id}/position")
        return decimal.Decimal(str(pos["index"]))

    idx1 = get_index(id1)
    idx2 = get_index(id2)
    idx3 = get_index(id3)
    assert idx1 < idx2 < idx3, f"Expected ascending indices, got {idx1} {idx2} {idx3}"

    # Move cl3 under cl1 (between cl1 and cl2, but from below — lower index)
    new_pos = req(f"api/checklist/{id3}/move/under/{id1}", "put")
    new_idx3 = decimal.Decimal(str(new_pos["index"]))
    assert new_idx3 < idx1, (
        f"After move/under cl1, cl3 index ({new_idx3}) should be below cl1 ({idx1})"
    )

    # Move cl1 above cl2 (above = higher index than cl2)
    new_pos = req(f"api/checklist/{id1}/move/above/{id2}", "put")
    new_idx1 = decimal.Decimal(str(new_pos["index"]))
    assert new_idx1 > idx2, (
        f"After move/above cl2, cl1 index ({new_idx1}) should be above cl2 ({idx2})"
    )

    # All three checklists still exist
    positions = req("api/position")
    all_ids = [p["checklist_id"] for p in positions["items"]]
    for cl_id in (id1, id2, id3):
        assert cl_id in all_ids, f"Checklist {cl_id} missing after moves"

    # Clean up
    for cl_id in (id1, id2, id3):
        req(f"api/checklist/{cl_id}", "delete")

def test_checklist_list_orders_pinned_first():
    """A pinned checklist must appear before unpinned ones in the list endpoint,
    even when its index is the lowest (it would otherwise sort last)."""
    cl1 = req("api/checklist", "post", b={"name": "Pin-CL1"})  # lowest index
    cl2 = req("api/checklist", "post", b={"name": "Pin-CL2"})
    cl3 = req("api/checklist", "post", b={"name": "Pin-CL3"})  # highest index
    id1, id2, id3 = cl1["id"], cl2["id"], cl3["id"]

    # Unpinned, indices ascending -> list (desc index) yields cl3, cl2, cl1.
    def order_positions(*ids):
        items = req("api/checklist", q={"limit": 200, "offset": 0})["items"]
        order = {it["id"]: i for i, it in enumerate(items)}
        return [order[i] for i in ids]

    p3, p2, p1 = order_positions(id3, id2, id1)
    assert p3 < p2 < p1, f"Expected cl3<cl2<cl1 by index, got {p3} {p2} {p1}"

    # Pin the lowest-index checklist; it must now lead the others.
    req(f"api/checklist/{id1}/position", "patch", b={"pinned": True})
    p1, p3, p2 = order_positions(id1, id3, id2)
    assert p1 < p3 and p1 < p2, (
        f"Pinned cl1 (pos {p1}) should precede unpinned cl3 ({p3}) and cl2 ({p2})"
    )

    for cl_id in (id1, id2, id3):
        req(f"api/checklist/{cl_id}", "delete")


def test_checklist_move_under_places_below_target():
    """move/under always places the moved checklist at a lower index than the target."""
    cl_a = req("api/checklist", "post", b={"name": "Move-EdgeA"})
    cl_b = req("api/checklist", "post", b={"name": "Move-EdgeB"})
    id_a, id_b = cl_a["id"], cl_b["id"]

    idx_a = decimal.Decimal(str(req(f"api/checklist/{id_a}/position")["index"]))

    new_pos = req(f"api/checklist/{id_b}/move/under/{id_a}", "put")
    new_idx_b = decimal.Decimal(str(new_pos["index"]))
    assert new_idx_b < idx_a, (
        f"After move/under, cl_b index ({new_idx_b}) should be below cl_a ({idx_a})"
    )

    for cl_id in (id_a, id_b):
        req(f"api/checklist/{cl_id}", "delete")
