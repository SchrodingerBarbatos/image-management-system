import io

from flask import Flask

import routes.export as export_module


class LogicalSizedFile:
    def __init__(self, size):
        self.size = size
        self.position = 0

    def seek(self, offset, whence=0):
        if whence == 2:
            self.position = self.size + offset
        else:
            self.position = offset
        return self.position

    def tell(self):
        return self.position


def _client():
    app = Flask(__name__)
    app.register_blueprint(export_module.export_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


def test_excel_upload_limit_is_50_mb():
    assert export_module._MAX_EXCEL_UPLOAD_BYTES == 50 * 1024 * 1024


def test_excel_upload_accepts_exactly_50_mb():
    file = LogicalSizedFile(export_module._MAX_EXCEL_UPLOAD_BYTES)

    assert export_module._excel_upload_too_large(file) is False
    assert file.tell() == 0


def test_excel_upload_detects_files_over_50_mb():
    file = LogicalSizedFile(export_module._MAX_EXCEL_UPLOAD_BYTES + 1)

    assert export_module._excel_upload_too_large(file) is True
    assert file.tell() == 0


def test_excel_upload_rejects_files_over_50_mb(monkeypatch):
    monkeypatch.setattr(export_module, "_excel_upload_too_large", lambda _file: True)

    response = _client().post(
        "/api/export/excel",
        data={"file": (io.BytesIO(b"placeholder"), "too-large.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "文件大小不能超过 50MB"}
