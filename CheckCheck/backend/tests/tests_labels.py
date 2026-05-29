from _single_test_file_runner import run_all_tests_if_test_file_called

if __name__ == "__main__":
    run_all_tests_if_test_file_called()

from utils import req, dict_must_contain


# ── Label CRUD ────────────────────────────────────────────────────────────────


def test_label_create_and_list():
    # Create two labels (no color)
    a = req("api/label", "post", b={"display_name": "urgent"})
    dict_must_contain(a, required_keys=["id", "display_name", "sort_order"])
    assert a["display_name"] == "urgent"

    b = req("api/label", "post", b={"display_name": "later"})

    labels = req("api/label")
    ids = [l["id"] for l in labels]
    assert a["id"] in ids and b["id"] in ids, "Both labels must appear in list"


def test_label_create_with_color():
    colors = req("api/color")
    assert len(colors) > 0, "Need at least one color in the system"
    color_id = colors[0]["id"]

    label = req("api/label", "post", b={"display_name": "colored", "color_id": color_id})
    dict_must_contain(label, {"display_name": "colored"})
    assert label["color"] is not None
    assert label["color"]["id"] == color_id


def test_label_update():
    label = req("api/label", "post", b={"display_name": "old name"})
    label_id = label["id"]

    updated = req(f"api/label/{label_id}", "patch", b={"display_name": "new name"})
    dict_must_contain(updated, {"display_name": "new name"})

    labels = req("api/label")
    match = next((l for l in labels if l["id"] == label_id), None)
    assert match is not None
    assert match["display_name"] == "new name"


def test_label_delete():
    label = req("api/label", "post", b={"display_name": "to be deleted"})
    label_id = label["id"]

    req(f"api/label/{label_id}", "delete")

    labels = req("api/label")
    ids = [l["id"] for l in labels]
    assert label_id not in ids, "Deleted label must not appear in list"


def test_label_sort():
    # Create three labels and sort them in reverse order
    x = req("api/label", "post", b={"display_name": "sort-x"})
    y = req("api/label", "post", b={"display_name": "sort-y"})
    z = req("api/label", "post", b={"display_name": "sort-z"})

    new_order = [z["id"], y["id"], x["id"]]
    result = req("api/label/sort", "put", b=new_order)
    result_ids = [l["id"] for l in result]
    assert result_ids.index(z["id"]) < result_ids.index(y["id"]) < result_ids.index(x["id"]), (
        "Labels must appear in the requested sort order"
    )


# ── Checklist-label assignment ────────────────────────────────────────────────


def test_checklist_label_assign_and_remove():
    label = req("api/label", "post", b={"display_name": "cl-label"})
    label_id = label["id"]

    checklist = req("api/checklist", "post", b={"name": "Labelled List"})
    cl_id = checklist["id"]

    # Assign label
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")

    cl_labels = req(f"api/checklist/{cl_id}/label")
    ids = [l["id"] for l in cl_labels]
    assert label_id in ids, "Assigned label must appear on checklist"

    # Idempotent: assigning again must not error
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")
    assert len(req(f"api/checklist/{cl_id}/label")) == 1, "Duplicate assignment must be deduplicated"

    # Remove label
    req(f"api/checklist/{cl_id}/label/{label_id}", "delete")
    cl_labels_after = req(f"api/checklist/{cl_id}/label")
    assert label_id not in [l["id"] for l in cl_labels_after], "Removed label must not appear"

    # Clean up
    req(f"api/checklist/{cl_id}", "delete")
    req(f"api/label/{label_id}", "delete")


def test_checklist_label_multiple_labels():
    a = req("api/label", "post", b={"display_name": "ml-a"})
    b = req("api/label", "post", b={"display_name": "ml-b"})
    checklist = req("api/checklist", "post", b={"name": "Multi-label List"})
    cl_id = checklist["id"]

    req(f"api/checklist/{cl_id}/label/{a['id']}", "put")
    req(f"api/checklist/{cl_id}/label/{b['id']}", "put")

    cl_labels = req(f"api/checklist/{cl_id}/label")
    ids = [l["id"] for l in cl_labels]
    assert a["id"] in ids and b["id"] in ids

    # Remove one; the other must remain
    req(f"api/checklist/{cl_id}/label/{a['id']}", "delete")
    cl_labels = req(f"api/checklist/{cl_id}/label")
    ids = [l["id"] for l in cl_labels]
    assert a["id"] not in ids
    assert b["id"] in ids

    # Clean up
    req(f"api/checklist/{cl_id}", "delete")
    req(f"api/label/{a['id']}", "delete")
    req(f"api/label/{b['id']}", "delete")
