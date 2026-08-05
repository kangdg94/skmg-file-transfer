#!/usr/bin/env python3

import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pymysql


# =========================
# 설정
# =========================

START_DATE = "2025-01-01"

# 실행 당일까지 추출
END_DATE = datetime.now().strftime("%Y-%m-%d")

OUTPUT_DIR = Path("./daily_csv")

TABLE_NAME = "your_table"
DATE_COLUMN = "created_at"

# 필요한 컬럼만 명시하는 것을 권장
SELECT_COLUMNS = "*"

FETCH_SIZE = 10_000


def export_one_day(
    connection: pymysql.Connection,
    target_date: datetime,
) -> tuple[Path, int]:

    next_date = target_date + timedelta(days=1)

    start_datetime = target_date.strftime("%Y-%m-%d 00:00:00")
    end_datetime = next_date.strftime("%Y-%m-%d 00:00:00")

    file_date = target_date.strftime("%Y%m%d")
    output_file = OUTPUT_DIR / f"data_{file_date}.csv"

    query = f"""
        SELECT
            {SELECT_COLUMNS}
        FROM {TABLE_NAME}
        WHERE {DATE_COLUMN} >= %s
          AND {DATE_COLUMN} < %s
        ORDER BY {DATE_COLUMN}
    """

    row_count = 0

    with connection.cursor() as cursor:
        cursor.execute(query, (start_datetime, end_datetime))

        column_names = [
            column[0]
            for column in cursor.description
        ]

        with output_file.open(
            mode="w",
            newline="",
            encoding="utf-8-sig",
        ) as csv_file:

            writer = csv.writer(
                csv_file,
                quoting=csv.QUOTE_MINIMAL,
            )

            writer.writerow(column_names)

            while True:
                rows = cursor.fetchmany(FETCH_SIZE)

                if not rows:
                    break

                writer.writerows(rows)
                row_count += len(rows)

    return output_file, row_count


def main() -> None:
    start_date = datetime.strptime(
        START_DATE,
        "%Y-%m-%d",
    )

    end_date = datetime.strptime(
        END_DATE,
        "%Y-%m-%d",
    )

    if start_date > end_date:
        print("시작일이 종료일보다 늦습니다.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=30,
        read_timeout=3600,
        write_timeout=3600,
        autocommit=True,
    )

    try:
        current_date = start_date

        while current_date <= end_date:
            file_date = current_date.strftime("%Y-%m-%d")

            try:
                output_file, row_count = export_one_day(
                    connection,
                    current_date,
                )

                print(
                    f"[완료] {file_date}: "
                    f"{row_count:,}건 → {output_file}"
                )

            except Exception as error:
                print(
                    f"[실패] {file_date}: {error}",
                    file=sys.stderr,
                )

                # 특정 날짜 실패 시 전체 작업을 중단하려면 아래 활성화
                # raise

            current_date += timedelta(days=1)

    finally:
        connection.close()


if __name__ == "__main__":
    main()