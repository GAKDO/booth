"""S3ObjectStore가 MinIO 응답을 해석하는 방식 (botocore Stubber, 네트워크 없음)."""

import pytest
from botocore.stub import Stubber

from trace_booth.uploader import S3ObjectStore


@pytest.fixture
def s3():
    store = S3ObjectStore("http://minio.test", "test-access", "test-secret", "trace-raw")
    with Stubber(store._client) as stub:
        yield store, stub


def test_객체가_없으면_None(s3):
    store, stub = s3
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    assert store.head("MB01/x.jpg") is None


def test_객체가_있으면_메타데이터를_돌려준다(s3):
    store, stub = s3
    stub.add_response("head_object", {"Metadata": {"sha256": "ab"}}, {"Bucket": "trace-raw", "Key": "MB01/x.jpg"})
    assert store.head("MB01/x.jpg") == {"sha256": "ab"}


def test_권한_오류는_다시_시도하도록_예외로_올린다(s3):
    store, stub = s3
    stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(Exception, match="403"):
        store.head("MB01/x.jpg")
