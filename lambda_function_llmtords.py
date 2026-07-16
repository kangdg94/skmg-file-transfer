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


# ---------------------------------------------------------------------
# MySQL UPSERT
# ---------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO airbot_ai_log (
    source_event_id,
    topic,
    message_prefix,
    correlation_id,
    reply_to,
    request_log_id,
    request_log_meta,
    request_log_type,
    serial_number,
    event_timestamp,
    source_bucket,
    source_object_key,
    source_etag,
    source_record_index
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    topic = VALUES(topic),
    message_prefix = VALUES(message_prefix),
    correlation_id = VALUES(correlation_id),
    reply_to = VALUES(reply_to),
    request_log_id = VALUES(request_log_id),
    request_log_meta = VALUES(request_log_meta),
    request_log_type = VALUES(request_log_type),
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
    request_data = record.get("request")

    if (
        isinstance(request_data, dict)
        and nested_key in request_data
    ):
        return request_data.get(nested_key)

    return record.get(dotted_key)


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

    # request.log_content는 의도적으로 읽지 않는다.
    # 따라서 RDS에도 적재되지 않는다.

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
        request_log_type,
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

    raw_body = response["Body"].read()

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

    values = [
        transform_record(
            record=record,
            bucket=bucket,
            object_key=object_key,
            etag=etag,
            record_index=index,
        )
        for index, record in enumerate(records)
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

        if not object_key.lower().endswith(".json"):
            LOGGER.info(
                "JSON 파일이 아니므로 건너뜁니다: %s",
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