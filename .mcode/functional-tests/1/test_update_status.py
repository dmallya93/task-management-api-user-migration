"""PUT /api/v1/tasks/{id} and PATCH /api/v1/tasks/{id}/status"""
import requests

from conftest import TASKS, TIMEOUT, raw

JSON_HDR = {"Content-Type": "application/json"}


class TestUpdate:
    def test_update_fields(self, observe, make_task):
        tid = make_task(title="ftrun upd", tags=["a"]).json()["id"]
        r = observe(requests.put(f"{TASKS}/{tid}", json={
            "title": "new", "description": "nd", "priority": "high", "tags": ["b", "c"],
            "assigned_to": "zed", "due_date": "2031-02-03T04:05:06Z"}, timeout=TIMEOUT))
        assert r.status_code == 200
        b = r.json()
        assert (b["title"], b["description"], b["priority"], b["tags"], b["assigned_to"], b["due_date"]) == \
            ("new", "nd", "high", ["b", "c"], "zed", "2031-02-03T04:05:06Z")
        assert b["updated_at"] >= b["created_at"]

    def test_update_empty_tags_and_null(self, observe, make_task):
        tid = make_task(title="ftrun tags", tags=["a"]).json()["id"]
        r1 = observe(requests.put(f"{TASKS}/{tid}", json={"tags": None}, timeout=TIMEOUT), "null-tags")
        assert r1.status_code == 200 and r1.json()["tags"] == ["a"]
        r2 = observe(requests.put(f"{TASKS}/{tid}", json={"tags": []}, timeout=TIMEOUT), "empty-tags")
        assert r2.status_code == 200 and r2.json()["tags"] == []

    def test_update_invalid_status_partial_mutation(self, observe, make_task):
        tid = make_task(title="ftrun partial").json()["id"]
        r = observe(requests.put(f"{TASKS}/{tid}", json={"title": "mutated", "status": "bogus"},
                                 timeout=TIMEOUT), "put")
        assert r.status_code == 400
        assert r.text == '{"error":{"code":"VALIDATION_ERROR","message":"invalid status: bogus"}}'
        g = observe(requests.get(f"{TASKS}/{tid}", timeout=TIMEOUT), "get")
        assert g.json()["title"] == "mutated"  # Go mutates title before failing on status

    def test_update_invalid_priority(self, observe, make_task):
        tid = make_task(title="ftrun prio").json()["id"]
        r = observe(requests.put(f"{TASKS}/{tid}", json={"status": "in_progress", "priority": "nope"},
                                 timeout=TIMEOUT), "put")
        assert r.status_code == 400
        assert r.text == '{"error":{"code":"VALIDATION_ERROR","message":"invalid priority: nope"}}'
        g = observe(requests.get(f"{TASKS}/{tid}", timeout=TIMEOUT), "get")
        assert g.json()["status"] == "in_progress"

    def test_update_not_found(self, observe):
        r = observe(requests.put(f"{TASKS}/nope-id", json={"title": "x"}, timeout=TIMEOUT))
        assert r.status_code == 404
        assert r.text == '{"error":{"code":"NOT_FOUND","message":"task with ID nope-id not found"}}'

    def test_update_empty_title(self, observe, make_task):
        tid = make_task(title="ftrun et").json()["id"]
        r = observe(requests.put(f"{TASKS}/{tid}", json={"title": ""}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.text == ('{"error":{"code":"VALIDATION_ERROR","message":"Key: \'UpdateTaskRequest.Title\' '
                          'Error:Field validation for \'Title\' failed on the \'min\' tag"}}')

    def test_update_empty_body(self, observe, make_task):
        tid = make_task(title="ftrun eb").json()["id"]
        r = observe(raw("PUT", f"{TASKS}/{tid}", data=b"", headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == '{"error":{"code":"VALIDATION_ERROR","message":"EOF"}}'

    def test_update_wrong_type(self, observe, make_task):
        tid = make_task(title="ftrun wt").json()["id"]
        r = observe(raw("PUT", f"{TASKS}/{tid}", data=b'{"tags":[1]}', headers=JSON_HDR))
        assert r.status_code == 400
        assert "cannot unmarshal number into Go struct field UpdateTaskRequest.tags of type string" in r.text

    def test_update_completed_at_set_once(self, observe, make_task):
        tid = make_task(title="ftrun comp").json()["id"]
        r1 = observe(requests.put(f"{TASKS}/{tid}", json={"status": "completed"}, timeout=TIMEOUT), "complete")
        assert r1.status_code == 200
        c1 = r1.json()["completed_at"]
        assert list(r1.json().keys())[-1] == "completed_at"
        r2 = observe(requests.put(f"{TASKS}/{tid}", json={"status": "pending"}, timeout=TIMEOUT), "reopen")
        assert r2.json()["completed_at"] == c1
        r3 = observe(requests.put(f"{TASKS}/{tid}", json={"status": "completed"}, timeout=TIMEOUT), "recomplete")
        assert r3.json()["completed_at"] == c1


class TestPatchStatus:
    def test_patch_status_ok(self, observe, make_task):
        tid = make_task(title="ftrun patch").json()["id"]
        r = observe(requests.patch(f"{TASKS}/{tid}/status", json={"status": "on_hold"}, timeout=TIMEOUT))
        assert r.status_code == 200
        assert r.json()["status"] == "on_hold"
        assert "completed_at" not in r.json()

    def test_patch_status_completed(self, observe, make_task):
        tid = make_task(title="ftrun patchc").json()["id"]
        r = observe(requests.patch(f"{TASKS}/{tid}/status", json={"status": "completed"}, timeout=TIMEOUT))
        assert r.status_code == 200
        assert "completed_at" in r.json()

    def test_patch_status_invalid_before_lookup(self, observe):
        r = observe(requests.patch(f"{TASKS}/missing/status", json={"status": "bogus"}, timeout=TIMEOUT))
        assert r.status_code == 400
        assert r.text == '{"error":{"code":"VALIDATION_ERROR","message":"invalid status: bogus"}}'

    def test_patch_status_missing(self, observe, make_task):
        tid = make_task(title="ftrun pm").json()["id"]
        r = observe(raw("PATCH", f"{TASKS}/{tid}/status", data=b"{}", headers=JSON_HDR))
        assert r.status_code == 400
        assert r.text == ('{"error":{"code":"VALIDATION_ERROR","message":"Key: \'UpdateTaskStatusRequest.Status\' '
                          'Error:Field validation for \'Status\' failed on the \'required\' tag"}}')

    def test_patch_status_not_found(self, observe):
        r = observe(requests.patch(f"{TASKS}/missing/status", json={"status": "pending"}, timeout=TIMEOUT))
        assert r.status_code == 404
        assert r.text == '{"error":{"code":"NOT_FOUND","message":"task with ID missing not found"}}'

    def test_patch_status_wrong_type(self, observe):
        r = observe(raw("PATCH", f"{TASKS}/x/status", data=b'{"status":5}', headers=JSON_HDR))
        assert r.status_code == 400
        assert ("json: cannot unmarshal number into Go struct field UpdateTaskStatusRequest.status "
                "of type models.TaskStatus") in r.text
