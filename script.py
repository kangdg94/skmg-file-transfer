import subprocess
from datetime import datetime, timedelta


START_DATE = "2026-03-01"
END_DATE = "2026-06-04"  # 원하는 종료일로 변경


def main():
    start_date = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end_date = datetime.strptime(END_DATE, "%Y-%m-%d").date()

    current_date = start_date

    while current_date <= end_date:
        stat_date = current_date.strftime("%Y-%m-%d")

        print(f"[START] stat_date={stat_date}")

        subprocess.run(
            [
                "python",
                "daily_clean_area_stat_cli.py",
                "--stat-date",
                stat_date,
            ],
            check=True,
        )

        print(f"[DONE] stat_date={stat_date}")

        current_date += timedelta(days=1)


if __name__ == "__main__":
    main()