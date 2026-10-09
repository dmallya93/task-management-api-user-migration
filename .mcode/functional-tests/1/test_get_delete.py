"""GET / DELETE /api/v1/tasks/{id}"""
import requests

from conftest import TASKS, TIMEOUT


class TestGetDelete:
    def test_get_existing(self, observe, make_task):
        created = make_task(title="ftrun get", tags=["g"], assigned_to="amy")
        tid = created.json()["id"]
        r = observe(requests.get(f"{TASKS}/{tid}", timeout=TIMEOUT))
        assert r.status_code == 200
        assert r.text == created.text

    def test_get_not_found(self, observe):
        r = observe(requests.get(f"{TASKS}/does-not-exist", timeout=TIMEOUT))
        assert r.status_code == 404
        assert r.text == '{"error":{"code":"NOT_FOUND","message":"task with ID does-not-exist not found"}}'

    def test_delete_then_gone(self, observe):
        c = requests.post(TASKS, json={"title": "ftrun delete"}, timeout=TIMEOUT)
        assert c.status_code == 201
        tid = c.json()["id"]
        d = observe(requests.delete(f"{TASKS}/{tid}", timeout=TIMEOUT), "delete")
        assert d.status_code == 204
        assert d.text == ""
        g = observe(requests.get(f"{TASKS}/{tid}", timeout=TIMEOUT), "get-after")
        assert g.status_code == 404
        d2 = observe(requests.delete(f"{TASKS}/{tid}", timeout=TIMEOUT), "delete-again")
        assert d2.status_code == 404
        assert '"code":"NOT_FOUND"' in d2.text
