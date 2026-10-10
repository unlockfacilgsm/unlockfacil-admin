import re
import pyotp
from app import create_app

def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)

def login(client, username="owner", password="a-very-long-test-password-123"):
    response = client.get("/admin/login")
    token = csrf_from(response.get_data(as_text=True))
    return client.post("/admin/login", data={
        "csrf_token": token,
        "totp_code": pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    }, follow_redirects=True)

def make_app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test.sqlite3"),
        "ADMIN_USERNAME": "owner",
        "ADMIN_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "COOKIE_SECURE": False,
    })

def test_admin_requires_login_and_has_security_headers(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

def test_login_csrf_and_logout_revokes_session(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    response = login(client)
    assert response.status_code == 200
    assert b"Vis" in response.data or b"Faturamento recebido" in response.data
    page = client.get("/admin")
    token = csrf_from(page.get_data(as_text=True))
    out = client.post("/admin/logout", data={"csrf_token": token}, follow_redirects=False)
    assert out.status_code == 302
    assert client.get("/admin").status_code == 302

def test_wrong_totp_does_not_authenticate(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    page = client.get("/admin/login")
    response = client.post("/admin/login", data={"csrf_token": csrf_from(page.get_data(as_text=True)), "totp_code": "000000"})
    assert response.status_code == 401
    assert client.get("/admin").status_code == 302

def test_sale_is_persisted_and_can_be_cancelled_without_deletion(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    login(client)
    page = client.get("/admin/vendas")
    token = csrf_from(page.get_data(as_text=True))
    response = client.post("/admin/vendas", data={
        "csrf_token": token,
        "tool": "Unlock Tool",
        "plan": "12 horas",
        "amount": "20,00",
        "sold_on": "2026-10-09",
        "phone": "5511999999999",
        "account_login": "cliente@example.com",
        "account_password": "senha-teste",
        "payment_method": "Pix",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Unlock Tool" in response.data
    page = client.get("/admin/vendas")
    token = csrf_from(page.get_data(as_text=True))
    import re
    match = re.search(r'action="/admin/vendas/(\d+)/cancelar"', page.get_data(as_text=True))
    assert match
    response = client.post(f"/admin/vendas/{match.group(1)}/cancelar",
                           data={"csrf_token": token}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        from app import get_db
        row = get_db().execute("SELECT status FROM sales WHERE id=?", (int(match.group(1)),)).fetchone()
        assert row["status"] == "Cancelado"

def test_csrf_is_required_for_write_operations(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    login(client)
    response = client.post("/admin/catalogo", data={"tool": "Unlock Tool", "plan": "12 horas"})
    assert response.status_code == 400


def test_totp_is_required(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    page = client.get("/admin/login")
    token = csrf_from(page.get_data(as_text=True))
    response = client.post("/admin/login", data={
        "csrf_token": token,
        "username": "owner",
        "password": "a-very-long-test-password-123",
        "totp_code": "abcdef",
    })
    assert response.status_code == 401
    assert client.get("/admin").status_code == 302

def test_invalid_plan_is_rejected(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    login(client)
    page = client.get("/admin/vendas")
    token = csrf_from(page.get_data(as_text=True))
    response = client.post("/admin/vendas", data={
        "csrf_token": token, "tool": "Unlock Tool", "plan": "plano-inventado",
        "amount": "20,00", "sold_on": "2026-10-09", "phone": "5511999999999",
        "account_login": "cliente@example.com", "account_password": "senha-teste", "payment_method": "Pix",
    })
    assert response.status_code == 400
