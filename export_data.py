#!/usr/bin/env python3

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pymysql
from pymysql.connections import Connection


TABLE_NAME = "benjamin.history_airbot_air_quality"
DATE_COLUMN = "create_date"

# 한 번에 메모리에 읽는 행 수
FETCH_SIZE = 10_000


def parse_date(value: str) -> date:
    """YYYY-MM-DD 문자열을 date 객체로 변환한다."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 잘못되었습니다: {value}. YYYY-MM-DD 형식으로 입력하세요."
        ) from exc


def safe_filename(value: str) -> str:
    """파일명에 사용할 수 없는 문자를 치환한다."""
    invalid_chars = '<>:"/\\|?*'
    result = value

    for char in invalid_chars:
        result = result.replace(char, "_")

    return result.strip() or "unknown"


def create_connection(args: argparse.Namespace) -> Connection:
    """Aurora MySQL 연결을 생성한다."""
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database="benjamin",
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
        cursorclass=pymysql.cursors.SSCursor,
    )


def export_one_day(
    connection: Connection,
    serial: str,
    target_date: date,
    output_directory: Path,
    overwrite: bool,
    keep_empty: bool,
) -> tuple[Optional[Path], int]:
    """
    지정한 날짜 하루치 데이터를 CSV로 저장한다.

    조회 범위:
      target_date 00:00:00 이상
      다음 날 00:00:00 미만
    """
    next_date = target_date + timedelta(days=1)

    start_datetime = datetime.combine(target_date, datetime.min.time())
    end_datetime = datetime.combine(next_date, datetime.min.time())

    serial_filename = safe_filename(serial)
    output_file = output_directory / (
    f"{serial_filename}_{target_date:%Y%m%d}.csv"
    )

    temporary_file = output_file.with_suffix(".csv.tmp")

    if output_file.exists() and not overwrite:
        return output_file, -1

    query = f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE serial = %s
          AND {DATE_COLUMN} >= %s
          AND {DATE_COLUMN} < %s
        ORDER BY {DATE_COLUMN}
    """

    row_count = 0

    try:
        # 장시간 실행 중 연결이 끊겼다면 재연결
        connection.ping(reconnect=True)

        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    serial,
                    start_datetime,
                    end_datetime,
                ),
            )

            if cursor.description is None:
                raise RuntimeError("조회 결과의 컬럼 정보를 가져오지 못했습니다.")

            column_names = [
                column[0]
                for column in cursor.description
            ]

            with temporary_file.open(
                mode="w",
                newline="",
                encoding="utf-8-sig",
            ) as csv_file:
                writer = csv.writer(
                    csv_file,
                    delimiter=",",
                    quotechar='"',
                    quoting=csv.QUOTE_MINIMAL,
                    lineterminator="\n",
                )

                writer.writerow(column_names)

                while True:
                    rows = cursor.fetchmany(FETCH_SIZE)

                    if not rows:
                        break

                    writer.writerows(rows)
                    row_count += len(rows)

        if row_count == 0 and not keep_empty:
            temporary_file.unlink(missing_ok=True)
            output_file.unlink(missing_ok=True)
            return None, 0

        # 정상 완료된 경우에만 최종 파일명으로 변경
        temporary_file.replace(output_file)

        return output_file, row_count

    except Exception:
        # 실패한 날짜의 불완전한 파일 제거
        temporary_file.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aurora MySQL의 history_airbot_air_quality 데이터를 "
            "날짜별 CSV 파일로 추출합니다."
        )
    )

    parser.add_argument(
        "--host",
        default=os.getenv("DB_HOST"),
        help="Aurora MySQL 엔드포인트. 환경변수 DB_HOST 사용 가능",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DB_PORT", "3306")),
        help="MySQL 포트. 기본값: 3306",
    )

    parser.add_argument(
        "--user",
        default=os.getenv("DB_USER"),
        help="DB 사용자명. 환경변수 DB_USER 사용 가능",
    )

    parser.add_argument(
        "--password",
        default=os.getenv("DB_PASSWORD"),
        help="DB 비밀번호. 환경변수 DB_PASSWORD 사용 가능",
    )

    parser.add_argument(
        "--serial",
        nargs="+",
        required=True,
        help="조회할 serial 값 목록. 예: test1 test2 test3",
    )

    parser.add_argument(
        "--start-date",
        required=True,
        type=parse_date,
        help="추출 시작일. 형식: YYYY-MM-DD",
    )

    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=date.today(),
        help="추출 종료일. 기본값: 실행 당일",
    )

    parser.add_argument(
        "--output-dir",
        default="./daily_csv",
        help="CSV 저장 폴더. 기본값: ./daily_csv",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="기존 CSV 파일이 있어도 다시 생성",
    )

    parser.add_argument(
        "--keep-empty",
        action="store_true",
        help="데이터가 없는 날짜에도 헤더만 있는 CSV 생성",
    )

    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="특정 날짜에서 오류 발생 시 전체 작업 중단",
    )

    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=30,
        help="DB 연결 제한 시간(초). 기본값: 30",
    )

    parser.add_argument(
        "--read-timeout",
        type=int,
        default=3600,
        help="DB 조회 제한 시간(초). 기본값: 3600",
    )

    parser.add_argument(
        "--write-timeout",
        type=int,
        default=3600,
        help="DB 쓰기 제한 시간(초). 기본값: 3600",
    )

    return parser


def validate_args(args: argparse.Namespace) -> None:
    missing_values = []

    if not args.host:
        missing_values.append("--host 또는 DB_HOST")
    if not args.user:
        missing_values.append("--user 또는 DB_USER")
    if not args.password:
        missing_values.append("--password 또는 DB_PASSWORD")

    if missing_values:
        raise ValueError(
            "다음 접속 정보가 없습니다: "
            + ", ".join(missing_values)
        )

    if args.start_date > args.end_date:
        raise ValueError(
            f"시작일({args.start_date})이 종료일({args.end_date})보다 늦습니다."
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    output_directory = Path(args.output_dir).expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print("Aurora MySQL 날짜별 CSV 추출")
    print("==================================================")
    print(f"DB Host      : {args.host}")
    print(f"DB Port      : {args.port}")
    print(f"DB User      : {args.user}")
    print(f"Serial 목록  : {', '.join(args.serial)}")
    print(f"기간         : {args.start_date} ~ {args.end_date}")
    print(f"저장 경로    : {output_directory}")
    print(f"기존 파일 덮어쓰기: {args.overwrite}")
    print("==================================================")

    success_count = 0
    skipped_count = 0
    empty_count = 0
    failed_count = 0
    total_rows = 0

    connection: Optional[Connection] = None

    try:
        connection = create_connection(args)
        print("[연결 성공] Aurora MySQL")

        for serial in args.serial:
            print("")
            print("==================================================")
            print(f"Serial 추출 시작: {serial}")
            print("==================================================")

            current_date = args.start_date

            while current_date <= args.end_date:
                display_date = current_date.isoformat()

                try:
                    print(
                        f"[시작] serial={serial}, "
                        f"date={display_date}"
                    )

                    output_file, row_count = export_one_day(
                        connection=connection,
                        serial=serial,
                        target_date=current_date,
                        output_directory=output_directory,
                        overwrite=args.overwrite,
                        keep_empty=args.keep_empty,
                    )

                    if row_count == -1:
                        skipped_count += 1

                        print(
                            f"[건너뜀] serial={serial}, "
                            f"date={display_date}: "
                            f"기존 파일 존재 → {output_file}"
                        )

                    elif row_count == 0:
                        empty_count += 1

                        if output_file is None:
                            print(
                                f"[데이터 없음] serial={serial}, "
                                f"date={display_date}: 파일 생성 안 함"
                            )
                        else:
                            print(
                                f"[데이터 없음] serial={serial}, "
                                f"date={display_date}: "
                                f"헤더 파일 생성 → {output_file}"
                            )

                    else:
                        success_count += 1
                        total_rows += row_count

                        print(
                            f"[완료] serial={serial}, "
                            f"date={display_date}: "
                            f"{row_count:,}건 → {output_file}"
                        )

                except Exception as exc:
                    failed_count += 1

                    print(
                        f"[실패] serial={serial}, "
                        f"date={display_date}: "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )

                    if args.stop_on_error:
                        raise

                    try:
                        connection.ping(reconnect=True)
                    except Exception:
                        try:
                            connection.close()
                        except Exception:
                            pass

                        connection = create_connection(args)

                current_date += timedelta(days=1)

    except KeyboardInterrupt:
        print("\n[중단] 사용자에 의해 작업이 중단되었습니다.")
        return 130

    except Exception as exc:
        print(
            f"[전체 작업 실패] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    finally:
        if connection is not None:
            connection.close()

    print("")
    print("==================================================")
    print("추출 결과")
    print("==================================================")
    print(f"정상 파일       : {success_count:,}개")
    print(f"기존 파일 건너뜀: {skipped_count:,}개")
    print(f"데이터 없는 날짜: {empty_count:,}개")
    print(f"실패한 날짜     : {failed_count:,}개")
    print(f"전체 추출 행 수 : {total_rows:,}건")
    print(f"저장 경로       : {output_directory}")
    print("==================================================")

    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())