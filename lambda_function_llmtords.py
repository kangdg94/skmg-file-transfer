import gzip
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

import boto3
import pymysql


# ---------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

S3_CLIENT = boto3.client("s3")

DB_CONNECTION = None

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

# timestamp에 타임존 정보가 없을 때 적용할 기준 시간대
SOURCE_TIMEZONE = ZoneInfo(
    os.environ.get("SOURCE_TIMEZONE", "Asia/Seoul")
)

TARGET_PREFIX = "skmg_airbot_v1_AILogData/"

# 일반 JSON과 gzip 압축 JSON 모두 지원
SUPPORTED_SUFFIXES = (
    ".json",
    ".json.gz",
    ".gz",
)


# ---------------------------------------------------------------------
# MySQL UPSERT
# ---------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO airbot_ai_function_token_log (
    source_event_id,
    topic,
    message_prefix,
    correlation_id,
    reply_to,
    request_log_id,
    request_log_meta,
    request_log_type,
    request_log_content,
    serial_number,
    event_timestamp,
    source_bucket,
    source_object_key,
    source_etag,
    source_record_index
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    topic = VALUES(topic),
    message_prefix = VALUES(message_prefix),
    correlation_id = VALUES(correlation_id),
    reply_to = VALUES(reply_to),
    request_log_id = VALUES(request_log_id),
    request_log_meta = VALUES(request_log_meta),
    request_log_type = VALUES(request_log_type),
    request_log_content = VALUES(request_log_content),
    serial_number = VALUES(serial_number),
    event_timestamp = VALUES(event_timestamp),
    source_bucket = VALUES(source_bucket),
    source_object_key = VALUES(source_object_key),
    source_etag = VALUES(source_etag),
    source_record_index = VALUES(source_record_index),
    updated_at = CURRENT_TIMESTAMP(3)
"""


def get_db_connection():
    """
    Lambda 실행 환경이 재사용될 경우 기존 DB 연결을 재사용한다.
    연결이 끊겼으면 자동으로 재연결한다.
    """
    global DB_CONNECTION

    if DB_CONNECTION is None:
        DB_CONNECTION = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=5,
            read_timeout=20,
            write_timeout=20,
        )
    else:
        DB_CONNECTION.ping(reconnect=True)

    return DB_CONNECTION


# ---------------------------------------------------------------------
# gzip 압축 해제
# ---------------------------------------------------------------------

def decompress_s3_object(
    raw_body: bytes,
    object_key: str,
    content_encoding: Optional[str] = None,
) -> bytes:
    """
    S3 객체가 gzip 형식이면 압축을 해제한다.

    감지 기준:
    1. 객체 키가 .gz로 끝나는 경우
    2. S3 Content-Encoding이 gzip인 경우
    3. gzip 매직 바이트(1f 8b)가 존재하는 경우

    일반 JSON 파일이면 원본 bytes를 그대로 반환한다.
    """
    normalized_encoding = (content_encoding or "").lower()

    is_gzip_extension = object_key.lower().endswith(".gz")
    is_gzip_encoding = "gzip" in normalized_encoding
    is_gzip_magic = raw_body.startswith(b"\x1f\x8b")

    if not (
        is_gzip_extension
        or is_gzip_encoding
        or is_gzip_magic
    ):
        return raw_body

    try:
        decompressed_body = gzip.decompress(raw_body)

        LOGGER.info(
            "gzip 압축 해제 완료: object_key=%s, "
            "compressed_size=%d, decompressed_size=%d",
            object_key,
            len(raw_body),
            len(decompressed_body),
        )

        return decompressed_body

    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise ValueError(
            "gzip 압축 해제에 실패했습니다. "
            f"object_key={object_key}"
        ) from exc


# ---------------------------------------------------------------------
# JSON 파싱
# ---------------------------------------------------------------------

def load_json_records(raw_body: bytes) -> List[Dict[str, Any]]:
    """
    다음 세 가지 파일 형식을 모두 처리한다.

    1. 단일 JSON 객체
    2. JSON 배열
    3. JSON Lines
    """
    text = raw_body.decode("utf-8-sig").strip()

    if not text:
        return []

    # 단일 JSON 또는 배열 시도
    try:
        payload = json.loads(text)

        if isinstance(payload, dict):
            return [payload]

        if isinstance(payload, list):
            if not all(isinstance(item, dict) for item in payload):
                raise ValueError(
                    "JSON 배열의 모든 항목은 객체여야 합니다."
                )

            return payload

        raise ValueError(
            "JSON 최상위 데이터는 객체 또는 배열이어야 합니다."
        )

    except json.JSONDecodeError:
        # JSON Lines 형식 처리
        records = []

        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                item = json.loads(line)

            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON Lines {line_number}번째 줄 오류: {exc}"
                ) from exc

            if not isinstance(item, dict):
                raise ValueError(
                    f"JSON Lines {line_number}번째 값은 "
                    f"JSON 객체여야 합니다."
                )

            records.append(item)

        return records


def get_nested_or_dotted_value(
    record: Dict[str, Any],
    nested_key: str,
    dotted_key: str,
) -> Any:
    """
    아래 두 형태를 모두 지원한다.

    현재 적재 원본은 평탄 키("request.log_type") 형태이므로
    dotted_key를 우선 조회하고, 없을 때만 중첩형을 조회한다.

    중첩형:
    {
        "request": {
            "log_ID": "..."
        }
    }

    평탄형:
    {
        "request.log_ID": "..."
    }
    """
    if dotted_key in record:
        return record.get(dotted_key)

    request_data = record.get("request")

    if (
        isinstance(request_data, dict)
        and nested_key in request_data
    ):
        return request_data.get(nested_key)

    return None


# ---------------------------------------------------------------------
# request.log_content 디코딩
# ---------------------------------------------------------------------

def is_log_type_one(value: Any) -> bool:
    """
    request.log_type이 숫자 1 또는 문자열 "1"인지 확인한다.
    """
    if value is None:
        return False

    return str(value).strip() == "1"


def decode_log_content(value: Any) -> Optional[str]:
    """
    request.log_content의 signed-byte 배열을 UTF-8 문자열로 변환한다.

    Athena/Trino에서 사용 중인 아래 변환식과 동일한 방식이다.

    from_utf8(
        from_hex(
            array_join(
                transform(
                    request.log_content,
                    x -> format(
                        '%02',
                        ((x % 256) + 256) % 256
                    )
                ),
                ' '
            )
        )
    )

    예:
    [72, 101, 108, 108, 111] -> "Hello"

    음수 값도 0~255 범위의 unsigned byte로 변환한다.

    UTF-8로 해석할 수 없는 바이트는 Athena의 from_utf8 동작과
    유사하게 유니코드 대체문자 U+FFFD로 치환한다.
    """
    if value is None:
        return None

    # JSON 문자열 안에 배열이 들어온 경우도 처리
    # 예: "[-19, -107, -100, -22, -72, -128]"
    if isinstance(value, str):
        stripped = value.strip()

        if not stripped:
            return ""

        try:
            parsed_value = json.loads(stripped)

        except json.JSONDecodeError as exc:
            raise ValueError(
                "request.log_content가 문자열인 경우 "
                "JSON 배열 형식이어야 합니다."
            ) from exc

        value = parsed_value

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            "request.log_content는 숫자 배열이어야 합니다. "
            f"actual_type={type(value).__name__}"
        )

    try:
        raw_bytes = bytes(
            ((int(item) % 256) + 256) % 256
            for item in value
        )

    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "request.log_content 배열에는 정수로 변환 가능한 값만 "
            "포함되어야 합니다."
        ) from exc

    return raw_bytes.decode(
        "utf-8",
        errors="replace",
    )


# ---------------------------------------------------------------------
# timestamp 처리
# ---------------------------------------------------------------------

def parse_timestamp(value: Any) -> datetime:
    """
    지원 형식:

    - epoch second
    - epoch millisecond
    - ISO-8601 문자열
    - 2026-07-16T21:00:00+09:00
    - 2026-07-16T12:00:00Z
    - 2026-07-16 21:00:00

    DB에는 UTC 기준 DATETIME으로 저장한다.
    """
    if value is None or value == "":
        raise ValueError("timestamp 값이 없습니다.")

    # 숫자 epoch timestamp
    if isinstance(value, (int, float)):
        seconds = (
            value / 1000
            if abs(value) >= 10_000_000_000
            else value
        )

        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc,
        ).replace(tzinfo=None)

    text = str(value).strip()

    # 문자열로 들어온 epoch timestamp
    try:
        numeric_value = float(text)

        seconds = (
            numeric_value / 1000
            if abs(numeric_value) >= 10_000_000_000
            else numeric_value
        )

        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc,
        ).replace(tzinfo=None)

    except ValueError:
        pass

    # Z 표기를 Python ISO 형식으로 변환
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)

    except ValueError as exc:
        raise ValueError(
            f"지원하지 않는 timestamp 형식입니다: {value}"
        ) from exc

    # 타임존 정보가 없으면 SOURCE_TIMEZONE 적용
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SOURCE_TIMEZONE)

    # UTC 변환 후 MySQL DATETIME에 맞게 tzinfo 제거
    return parsed.astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def to_mysql_json(value: Any) -> Optional[str]:
    """
    Python 객체를 MySQL JSON 컬럼에 넣을 문자열로 변환한다.
    """
    if value is None:
        return None

    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------
# 중복 방지 키
# ---------------------------------------------------------------------

def build_source_event_id(
    bucket: str,
    object_key: str,
    etag: str,
    record_index: int,
) -> str:
    """
    동일 S3 파일 이벤트가 중복 호출돼도 같은 키가 생성된다.
    """
    source = (
        f"{bucket}\n"
        f"{object_key}\n"
        f"{etag}\n"
        f"{record_index}"
    )

    return hashlib.sha256(
        source.encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------
# JSON → RDS 변환
# ---------------------------------------------------------------------

def transform_record(
    record: Dict[str, Any],
    bucket: str,
    object_key: str,
    etag: str,
    record_index: int,
) -> Tuple[Any, ...]:
    serial = record.get("serial")

    if serial is None or str(serial).strip() == "":
        raise ValueError(
            f"serial 값이 없습니다. record_index={record_index}"
        )

    request_log_id = get_nested_or_dotted_value(
        record=record,
        nested_key="log_ID",
        dotted_key="request.log_ID",
    )

    request_log_meta = get_nested_or_dotted_value(
        record=record,
        nested_key="log_meta",
        dotted_key="request.log_meta",
    )

    request_log_type = get_nested_or_dotted_value(
        record=record,
        nested_key="log_type",
        dotted_key="request.log_type",
    )

    # process_s3_object에서 1차 필터링을 수행하지만,
    # 방어적으로 여기서도 request.log_type=1만 허용한다.
    if not is_log_type_one(request_log_type):
        raise ValueError(
            "request.log_type이 1이 아닙니다. "
            f"record_index={record_index}, value={request_log_type}"
        )

    # request.log_type이 1인 경우에만
    # request.log_content를 디코딩하여 저장한다.
    request_log_content = None

    if is_log_type_one(request_log_type):
        encoded_log_content = get_nested_or_dotted_value(
            record=record,
            nested_key="log_content",
            dotted_key="request.log_content",
        )

        request_log_content = decode_log_content(
            encoded_log_content
        )

    return (
        build_source_event_id(
            bucket=bucket,
            object_key=object_key,
            etag=etag,
            record_index=record_index,
        ),
        record.get("topic"),
        record.get("prefix"),
        record.get("correlationId"),
        record.get("replyTo"),
        request_log_id,
        to_mysql_json(request_log_meta),
        "1",
        request_log_content,
        str(serial),
        parse_timestamp(record.get("timestamp")),
        bucket,
        object_key,
        etag,
        record_index,
    )


# ---------------------------------------------------------------------
# S3 파일 한 개 처리
# ---------------------------------------------------------------------

def process_s3_object(
    bucket: str,
    object_key: str,
    event_etag: Optional[str],
) -> int:
    response = S3_CLIENT.get_object(
        Bucket=bucket,
        Key=object_key,
    )

    compressed_body = response["Body"].read()

    raw_body = decompress_s3_object(
        raw_body=compressed_body,
        object_key=object_key,
        content_encoding=response.get("ContentEncoding"),
    )

    etag = (
        event_etag
        or response.get("ETag")
        or ""
    ).strip('"')

    records = load_json_records(raw_body)

    if not records:
        LOGGER.warning(
            "빈 JSON 파일입니다: s3://%s/%s",
            bucket,
            object_key,
        )
        return 0

    filtered_records = [
        (index, record)
        for index, record in enumerate(records)
        if is_log_type_one(
            get_nested_or_dotted_value(
                record=record,
                nested_key="log_type",
                dotted_key="request.log_type",
            )
        )
    ]

    LOGGER.info(
        "request_log_type=1 필터링: total=%d, matched=%d, "
        "object_key=%s",
        len(records),
        len(filtered_records),
        object_key,
    )

    if not filtered_records:
        return 0

    values = [
        transform_record(
            record=record,
            bucket=bucket,
            object_key=object_key,
            etag=etag,
            record_index=index,
        )
        for index, record in filtered_records
    ]

    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                UPSERT_SQL,
                values,
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    return len(values)


# ---------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------

def lambda_handler(event, context):
    event_records = event.get("Records", [])

    LOGGER.info(
        "S3 이벤트 수신: record_count=%d",
        len(event_records),
    )

    processed_files = 0
    processed_rows = 0

    for event_record in event_records:
        if event_record.get("eventSource") != "aws:s3":
            LOGGER.warning(
                "지원하지 않는 이벤트입니다: %s",
                event_record,
            )
            continue

        bucket = event_record["s3"]["bucket"]["name"]

        # S3 이벤트의 object key는 URL 인코딩돼서 전달될 수 있다.
        object_key = unquote_plus(
            event_record["s3"]["object"]["key"]
        )

        event_etag = event_record["s3"]["object"].get(
            "eTag"
        )

        # S3 트리거 설정이 잘못되더라도 대상 경로만 처리
        if not object_key.startswith(TARGET_PREFIX):
            LOGGER.info(
                "대상 경로가 아니므로 건너뜁니다: %s",
                object_key,
            )
            continue

        if not object_key.lower().endswith(SUPPORTED_SUFFIXES):
            LOGGER.info(
                "지원하지 않는 파일 형식이므로 건너뜁니다: %s",
                object_key,
            )
            continue

        LOGGER.info(
            "처리 시작: s3://%s/%s",
            bucket,
            object_key,
        )

        row_count = process_s3_object(
            bucket=bucket,
            object_key=object_key,
            event_etag=event_etag,
        )

        processed_files += 1
        processed_rows += row_count

        LOGGER.info(
            "처리 완료: object_key=%s, row_count=%d",
            object_key,
            row_count,
        )

    result = {
        "processedFiles": processed_files,
        "processedRows": processed_rows,
        "requestId": getattr(
            context,
            "aws_request_id",
            None,
        ),
    }

    LOGGER.info(
        "전체 처리 완료: %s",
        result,
    )

    return result