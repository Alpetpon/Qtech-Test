import pytest

from app.main import FrontendFiles, app

INDEX = '<div id="root"></div>'


@pytest.fixture
def frontend(tmp_path):
    """Собранный фронт во временной папке, подключён после API, как в рабочем образе."""
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")
    app.mount("/", FrontendFiles(directory=tmp_path))
    yield
    app.router.routes.pop()


def test_unknown_api_path_is_json_404(client):
    response = client.get("/api/unknown")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.usefixtures("frontend")
def test_frontend_does_not_shadow_api(client):
    assert client.get("/api/health").json() == {"status": "ok", "db": "ok"}

    response = client.get("/api/unknown")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.usefixtures("frontend")
@pytest.mark.parametrize("path", ["/", "/campaigns/1"])
def test_router_paths_get_index(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.text == INDEX


@pytest.mark.usefixtures("frontend")
def test_assets_are_served_as_files(client):
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == "console.log('app')"
