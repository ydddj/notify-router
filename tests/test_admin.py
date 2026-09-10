import importlib
from importlib.resources import files

from fastapi.testclient import TestClient

from notifyhub.store import Store, redact_secret_text


def test_login_page_does_not_prefill_admin_username():
    html = files("notifyhub").joinpath("static/index.html").read_text(encoding="utf-8")
    script = files("notifyhub").joinpath("static/app.js").read_text(encoding="utf-8")
    styles = files("notifyhub").joinpath("static/app.css").read_text(encoding="utf-8")

    assert '<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required>' in html
    assert "body.auth-pending #login-view,body.auth-pending #app{visibility:hidden}" in html
    assert "form.elements.username.value" not in script
    assert "page-skeleton" in script
    assert "notify-sidebar" in html
    assert 'id="i-sidebar-collapse"' in html
    assert 'id="i-sidebar-expand"' in html
    assert "--page-dim" in styles
    assert "--glass-dim" in styles
    assert "function toggleNavigation()" in script
    assert ".brand-mark, #user-avatar" in script
    assert script.index("<h2>界面质感</h2>") < script.index("<h2>运行信息</h2>")


def test_admin_login_returns_plaintext_config_and_can_queue_channel_test(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("NH_USER", "admin")
    monkeypatch.setenv("NH_PASSWORD", "test-password")
    main = importlib.import_module("notifyhub.main")
    test_store = Store(tmp_path)
    test_store.save_config(
        {
            "app": {},
            "channels": [{"name": "test", "type": "webhook", "config": {"webhook_url": "http://127.0.0.1:9"}}],
            "routes": [{"route_id": "r1", "route_name": "Route", "channel_name": ["test"], "active": True}],
        }
    )
    test_store.save_plugin_config("demo", "Demo", {"api_key": "plain-api-key", "app_secret": "plain-secret"})
    monkeypatch.setattr(main, "store", test_store)
    client = TestClient(main.app)
    try:
        assert client.get("/").status_code == 200
        assert client.get("/api/admin/config").status_code == 401
        response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        config = client.get("/api/admin/config").json()
        assert config["channels"][0]["config"]["webhook_url"] == "http://127.0.0.1:9"
        plugin_config = client.get("/api/admin/plugins/demo/config").json()
        assert plugin_config == {"api_key": "plain-api-key", "app_secret": "plain-secret"}
        response = client.post("/api/admin/channels/test/test", json={})
        assert response.status_code == 200
        assert test_store.delivery_status()[0]["status"] == "pending"
    finally:
        client.close()


def test_dashboard_stats_and_delivery_filter(tmp_path):
    store = Store(tmp_path)
    store.save_config(
        {
            "app": {},
            "channels": [{"name": "test", "type": "webhook", "config": {"url": "http://127.0.0.1:9"}}],
            "routes": [{"route_id": "r1", "route_name": "Route", "channel_name": ["test"], "active": True}],
        }
    )
    store.enqueue_router("r1", "title", "content")
    stats = store.dashboard_stats()
    assert stats["queue"] == {"pending": 1}
    assert store.delivery_status(status="pending")[0]["content"] == "content"
    assert store.delivery_status(status="failed") == []


def test_admin_event_types_lists_registered_types(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("NH_USER", "admin")
    monkeypatch.setenv("NH_PASSWORD", "test-password")
    main = importlib.import_module("notifyhub.main")
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    try:
        assert client.post("/api/admin/login", json={"username": "admin", "password": "test-password"}).status_code == 200
        response = client.get("/api/admin/event-types")
        assert response.status_code == 200
        values = response.json()["event_types"]
        assert any(item == {"value": "Emby.PlaybackPause", "label": "Emby · 暂停播放"} for item in values)
        assert any(item == {"value": "PVE.Backup", "label": "PVE · 备份"} for item in values)
    finally:
        client.close()


def test_admin_note_requires_login_and_persists_on_the_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("NH_USER", "admin")
    monkeypatch.setenv("NH_PASSWORD", "test-password")
    main = importlib.import_module("notifyhub.main")
    test_store = Store(tmp_path)
    monkeypatch.setattr(main, "store", test_store)
    client = TestClient(main.app)
    try:
        assert client.get("/api/admin/note").status_code == 401
        assert client.put("/api/admin/note", json={"note": "未登录"}).status_code == 401
        assert client.post("/api/admin/login", json={"username": "admin", "password": "test-password"}).status_code == 200
        assert client.get("/api/admin/note").json() == {"note": ""}

        response = client.put("/api/admin/note", json={"note": "周日维护通知服务"})
        assert response.status_code == 200
        assert Store(tmp_path).admin_note == "周日维护通知服务"
        assert client.get("/api/admin/note").json() == {"note": "周日维护通知服务"}
        assert client.put("/api/admin/note", json={"note": "x" * 10_001}).status_code == 400
    finally:
        client.close()


def test_appearance_settings_and_background_gallery_are_instance_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("NH_USER", "admin")
    monkeypatch.setenv("NH_PASSWORD", "test-password")
    main = importlib.import_module("notifyhub.main")
    test_store = Store(tmp_path)
    monkeypatch.setattr(main, "store", test_store)
    client = TestClient(main.app)
    try:
        public = client.get("/api/appearance")
        assert public.status_code == 200
        assert public.json()["appearance_glass_opacity"] == 50
        assert client.get("/api/admin/appearance").status_code == 401
        assert client.post("/api/admin/appearance/backgrounds", files={"background": ("bad.txt", b"text", "text/plain")}).status_code == 401

        assert client.post("/api/admin/login", json={"username": "admin", "password": "test-password"}).status_code == 200
        invalid = client.post("/api/admin/appearance/backgrounds", files={"background": ("bad.txt", b"text", "text/plain")})
        assert invalid.status_code == 400
        image = b"\x89PNG\r\n\x1a\n" + b"notify-router-background"
        uploaded = client.post("/api/admin/appearance/backgrounds", files={"background": ("background.png", image, "image/png")})
        assert uploaded.status_code == 200
        background = uploaded.json()["appearance_background"]
        assert background in uploaded.json()["appearance_backgrounds"]
        assert client.get(background).content == image

        saved = client.put(
            "/api/admin/appearance",
            json={
                "appearance_glass_opacity": 70,
                "appearance_glass_brightness": 60,
                "appearance_glass_blur": 18,
                "appearance_mask_opacity": 40,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["appearance_glass_blur"] == 18
        assert Store(tmp_path).appearance["appearance_background"] == background

        filename = background.rsplit("/", 1)[-1]
        deleted = client.delete(f"/api/admin/appearance/backgrounds/{filename}")
        assert deleted.status_code == 200
        assert deleted.json()["appearance_background"] == ""
        assert client.get(background).status_code == 404
    finally:
        client.close()


def test_log_and_delivery_errors_hide_embedded_tokens():
    value = "POST https://api.telegram.org/bot123456:ABC/sendMessage?access_token=secret Authorization: Bearer private"
    safe = redact_secret_text(value)
    assert "123456:ABC" not in safe
    assert "access_token=secret" not in safe
    assert "Bearer private" not in safe


def test_admin_login_is_rate_limited(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("NH_USER", "admin")
    monkeypatch.setenv("NH_PASSWORD", "test-password")
    main = importlib.import_module("notifyhub.main")
    main.LOGIN_FAILURES.clear()
    client = TestClient(main.app)
    for _ in range(10):
        assert client.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    main.LOGIN_FAILURES.clear()


def test_default_password_can_be_changed_without_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.delenv("NH_PASSWORD", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    main = importlib.import_module("notifyhub.main")
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    try:
        response = client.post("/api/admin/login", json={"username": "admin", "password": "password"})
        assert response.status_code == 200
        assert response.json()["password_change_required"] is True
        response = client.post(
            "/api/admin/password",
            json={
                "current_password": "password",
                "new_password": "correct horse battery",
                "confirm_password": "correct horse battery",
            },
        )
        assert response.status_code == 200
        assert client.get("/api/admin/config").status_code == 401
        assert client.post("/api/admin/login", json={"username": "admin", "password": "password"}).status_code == 401
        response = client.post("/api/admin/login", json={"username": "admin", "password": "correct horse battery"})
        assert response.status_code == 200
        assert response.json()["password_change_required"] is False
        assert (tmp_path / "conf" / "security.json").stat().st_mode & 0o777 == 0o600
    finally:
        client.close()
