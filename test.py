import logging
from datetime import datetime, timedelta, timezone

from db_config import get_connection

KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)


def run_daily_clean_area_stat(stat_date=None):
    target_date = get_target_date(stat_date)

    start_dt = f"{target_date} 00:00:00"
    end_dt = f"{target_date + timedelta(days=1)} 00:00:00"

    logger.info(
        "daily clean area stat started. stat_date=%s, start_dt=%s, end_dt=%s",
        target_date,
        start_dt,
        end_dt,
    )

    conn = get_connection()

    sql = """
    INSERT INTO history_clean_area_result_stat (
        stat_date,
        serial,
        call_aqm_type,
        clean_type_code,
        area_id,
        device_id,
        map_id,
        avg_before_aq_pm1,
        avg_before_aq_pm25,
        avg_before_aq_pm10,
        avg_before_aq_humidity,
        avg_before_aq_temperature,
        avg_before_aq_voc_index_level,
        avg_before_aq_nox_index_level,
        avg_before_aq_voc_raw_data,
        avg_before_aq_nox_raw_data,
        avg_before_aq_level,
        avg_before_fa_ppb,
        avg_before_fa_humidity,
        avg_before_fa_temperature,
        avg_before_fa_level,
        avg_before_co2_ppm,
        avg_before_co2_humidity,
        avg_before_co2_temperature,
        avg_before_co2_level,
        avg_before_aq_pm1_level,
        avg_before_aq_pm25_level,
        avg_before_aq_pm10_level,
        avg_after_aq_pm1,
        avg_after_aq_pm25,
        avg_after_aq_pm10,
        avg_after_aq_humidity,
        avg_after_aq_temperature,
        avg_after_aq_voc_index_level,
        avg_after_aq_nox_index_level,
        avg_after_aq_voc_raw_data,
        avg_after_aq_nox_raw_data,
        avg_after_aq_level,
        avg_after_fa_ppb,
        avg_after_fa_humidity,
        avg_after_fa_temperature,
        avg_after_fa_level,
        avg_after_co2_ppm,
        avg_after_co2_humidity,
        avg_after_co2_temperature,
        avg_after_co2_level,
        avg_after_aq_pm1_level,
        avg_after_aq_pm25_level,
        avg_after_aq_pm10_level,
        row_count,
        morning_count,
        day_count,
        evening_count,
        night_count,
        dawn_count
    )
    SELECT
        %s AS stat_date,
        hcr.serial,
        hcr.call_aqm_type,
        hcr.clean_type_code,
        hacr.area_id,
        MAX(hcr.device_id) AS device_id,
        MAX(hcr.map_id) AS map_id,
        AVG(hacr.before_aq_pm1),
        AVG(hacr.before_aq_pm25),
        AVG(hacr.before_aq_pm10),
        AVG(CAST(NULLIF(hacr.before_aq_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.before_aq_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.before_aq_voc_index_level),
        AVG(hacr.before_aq_nox_index_level),
        AVG(hacr.before_aq_voc_raw_data),
        AVG(hacr.before_aq_nox_raw_data),
        AVG(hacr.before_aq_level),
        AVG(hacr.before_fa_ppb),
        AVG(CAST(NULLIF(hacr.before_fa_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.before_fa_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.before_fa_level),
        AVG(hacr.before_co2_ppm),
        AVG(CAST(NULLIF(hacr.before_co2_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.before_co2_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.before_co2_level),
        AVG(hacr.before_aq_pm1_level),
        AVG(hacr.before_aq_pm25_level),
        AVG(hacr.before_aq_pm10_level),
        AVG(hacr.after_aq_pm1),
        AVG(hacr.after_aq_pm25),
        AVG(hacr.after_aq_pm10),
        AVG(CAST(NULLIF(hacr.after_aq_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.after_aq_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.after_aq_voc_index_level),
        AVG(hacr.after_aq_nox_index_level),
        AVG(hacr.after_aq_voc_raw_data),
        AVG(hacr.after_aq_nox_raw_data),
        AVG(hacr.after_aq_level),
        AVG(hacr.after_fa_ppb),
        AVG(CAST(NULLIF(hacr.after_fa_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.after_fa_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.after_fa_level),
        AVG(hacr.after_co2_ppm),
        AVG(CAST(NULLIF(hacr.after_co2_humidity, '') AS DECIMAL(18,4))),
        AVG(CAST(NULLIF(hacr.after_co2_temperature, '') AS DECIMAL(18,4))),
        AVG(hacr.after_co2_level),
        AVG(hacr.after_aq_pm1_level),
        AVG(hacr.after_aq_pm25_level),
        AVG(hacr.after_aq_pm10_level),
        COUNT(*) AS row_count,
        SUM(CASE WHEN HOUR(hacr.create_date) BETWEEN 6 AND 10 THEN 1 ELSE 0 END) AS morning_count,
        SUM(CASE WHEN HOUR(hacr.create_date) BETWEEN 11 AND 16 THEN 1 ELSE 0 END) AS day_count,
        SUM(CASE WHEN HOUR(hacr.create_date) BETWEEN 17 AND 20 THEN 1 ELSE 0 END) AS evening_count,
        SUM(CASE WHEN HOUR(hacr.create_date) BETWEEN 21 AND 23 THEN 1 ELSE 0 END) AS night_count,
        SUM(CASE WHEN HOUR(hacr.create_date) BETWEEN 0 AND 5 THEN 1 ELSE 0 END) AS dawn_count
    FROM history_clean_result hcr
    INNER JOIN history_area_clean_result hacr
        ON hacr.clean_result_id = hcr.id
    WHERE hacr.create_date >= %s
      AND hacr.create_date < %s
    GROUP BY
        hcr.serial,
        hcr.call_aqm_type,
        hcr.clean_type_code,
        hacr.area_id
    ON DUPLICATE KEY UPDATE
        device_id = VALUES(device_id),
        map_id = VALUES(map_id),
        avg_before_aq_pm1 = VALUES(avg_before_aq_pm1),
        avg_before_aq_pm25 = VALUES(avg_before_aq_pm25),
        avg_before_aq_pm10 = VALUES(avg_before_aq_pm10),
        avg_before_aq_humidity = VALUES(avg_before_aq_humidity),
        avg_before_aq_temperature = VALUES(avg_before_aq_temperature),
        avg_before_aq_voc_index_level = VALUES(avg_before_aq_voc_index_level),
        avg_before_aq_nox_index_level = VALUES(avg_before_aq_nox_index_level),
        avg_before_aq_voc_raw_data = VALUES(avg_before_aq_voc_raw_data),
        avg_before_aq_nox_raw_data = VALUES(avg_before_aq_nox_raw_data),
        avg_before_aq_level = VALUES(avg_before_aq_level),
        avg_before_fa_ppb = VALUES(avg_before_fa_ppb),
        avg_before_fa_humidity = VALUES(avg_before_fa_humidity),
        avg_before_fa_temperature = VALUES(avg_before_fa_temperature),
        avg_before_fa_level = VALUES(avg_before_fa_level),
        avg_before_co2_ppm = VALUES(avg_before_co2_ppm),
        avg_before_co2_humidity = VALUES(avg_before_co2_humidity),
        avg_before_co2_temperature = VALUES(avg_before_co2_temperature),
        avg_before_co2_level = VALUES(avg_before_co2_level),
        avg_before_aq_pm1_level = VALUES(avg_before_aq_pm1_level),
        avg_before_aq_pm25_level = VALUES(avg_before_aq_pm25_level),
        avg_before_aq_pm10_level = VALUES(avg_before_aq_pm10_level),
        avg_after_aq_pm1 = VALUES(avg_after_aq_pm1),
        avg_after_aq_pm25 = VALUES(avg_after_aq_pm25),
        avg_after_aq_pm10 = VALUES(avg_after_aq_pm10),
        avg_after_aq_humidity = VALUES(avg_after_aq_humidity),
        avg_after_aq_temperature = VALUES(avg_after_aq_temperature),
        avg_after_aq_voc_index_level = VALUES(avg_after_aq_voc_index_level),
        avg_after_aq_nox_index_level = VALUES(avg_after_aq_nox_index_level),
        avg_after_aq_voc_raw_data = VALUES(avg_after_aq_voc_raw_data),
        avg_after_aq_nox_raw_data = VALUES(avg_after_aq_nox_raw_data),
        avg_after_aq_level = VALUES(avg_after_aq_level),
        avg_after_fa_ppb = VALUES(avg_after_fa_ppb),
        avg_after_fa_humidity = VALUES(avg_after_fa_humidity),
        avg_after_fa_temperature = VALUES(avg_after_fa_temperature),
        avg_after_fa_level = VALUES(avg_after_fa_level),
        avg_after_co2_ppm = VALUES(avg_after_co2_ppm),
        avg_after_co2_humidity = VALUES(avg_after_co2_humidity),
        avg_after_co2_temperature = VALUES(avg_after_co2_temperature),
        avg_after_co2_level = VALUES(avg_after_co2_level),
        avg_after_aq_pm1_level = VALUES(avg_after_aq_pm1_level),
        avg_after_aq_pm25_level = VALUES(avg_after_aq_pm25_level),
        avg_after_aq_pm10_level = VALUES(avg_after_aq_pm10_level),
        row_count = VALUES(row_count),
        morning_count = VALUES(morning_count),
        day_count = VALUES(day_count),
        evening_count = VALUES(evening_count),
        night_count = VALUES(night_count),
        dawn_count = VALUES(dawn_count),
        update_date = CURRENT_TIMESTAMP
    """

    try:
        with conn.cursor() as cursor:
            affected_rows = cursor.execute(
                sql,
                (
                    target_date,
                    start_dt,
                    end_dt,
                ),
            )

        conn.commit()

        result = {
            "message": "daily clean area stat aggregation completed",
            "stat_date": str(target_date),
            "start_dt": start_dt,
            "end_dt": end_dt,
            "affected_rows": affected_rows,
        }

        logger.info("daily clean area stat completed. result=%s", result)
        return result

    except Exception:
        conn.rollback()
        logger.exception(
            "daily clean area stat failed. stat_date=%s, start_dt=%s, end_dt=%s",
            target_date,
            start_dt,
            end_dt,
        )
        raise

    finally:
        conn.close()
        logger.info("db connection closed.")


def get_target_date(stat_date=None):
    if stat_date:
        return datetime.strptime(stat_date, "%Y-%m-%d").date()

    return (datetime.now(KST) - timedelta(days=1)).date()
