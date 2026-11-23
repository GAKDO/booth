# 이 파일이 확인하는 것: 부스가 사진을 올리는 저장소(MinIO) 응답을 제대로 해석하는지. 네트워크 없이 가짜 응답으로 확인한다.

"""S3ObjectStore가 MinIO 응답을 해석하는 방식 (botocore Stubber, 네트워크 없음)."""

import pytest
from botocore.stub import Stubber

from trace_booth.uploader import S3ObjectStore


@pytest.fixture
def s3():
    store = S3ObjectStore("http://minio.test", "test-access", "test-secret", "trace-raw")
    with Stubber(store._client) as stub:
        yield store, stub


# [상황] 아직 올리지 않은 사진이 있는지 묻는다.
# [기대] "없음(None)"으로 답한다(올려야 할 사진).
def test_객체가_없으면_None(s3):
    store, stub = s3
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    assert store.head("MB01/x.jpg") is None


# [상황] 이미 올린 사진이 있는지 묻는다.
# [기대] 함께 저장한 정보(sha256 해시)를 돌려준다(같은 사진이면 다시 올리지 않음).
def test_객체가_있으면_메타데이터를_돌려준다(s3):
    store, stub = s3
    stub.add_response("head_object", {"Metadata": {"sha256": "ab"}}, {"Bucket": "trace-raw", "Key": "MB01/x.jpg"})
    assert store.head("MB01/x.jpg") == {"sha256": "ab"}


# [상황] 저장소가 권한 오류(403)를 낸다.
# [기대] "없음"으로 착각하지 않고 오류로 알린다(나중에 다시 시도하도록).
def test_권한_오류는_다시_시도하도록_예외로_올린다(s3):
    store, stub = s3
    stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(Exception, match="403"):
        store.head("MB01/x.jpg")
