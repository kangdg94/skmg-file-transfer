package com.skmagic.push.util;

import org.opencv.core.Point;
import org.opencv.core.MatOfPoint;
import org.opencv.core.MatOfPoint2f;
import org.opencv.imgproc.Imgproc;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 로봇(충전 스테이션) 위치가 포함된 방을 찾는 유틸. backend-api 의
 * {@code com.skmagic.api.map.service.RoomChecker} 와 동일 로직(OpenCV pointPolygonTest).
 */
public class RoomChecker {

    // 특정 좌표가 포함된 첫 번째 방 ID 반환
    public static String findFirstRoomIdContainingRobot(RoomListJson data) {

        try{
            // 검사할 좌표: charging_station.image_position
            Point stationPoint = new Point(
                    data.charging_station.image_position.x,
                    data.charging_station.image_position.y
            );

            String foundRoomId = null;

            for (Room room : data.room_list) {
                List<Point> polygon = new ArrayList<>();
                for (CoordinateData coord : room.image_path) {
                    polygon.add(new Point(coord.x, coord.y));
                }

                MatOfPoint matOfPoint = new MatOfPoint();
                matOfPoint.fromList(polygon);
                MatOfPoint2f matOfPoint2f = new MatOfPoint2f(matOfPoint.toArray());

                double result = Imgproc.pointPolygonTest(matOfPoint2f, stationPoint, false);

                if (result >= 0) { // 내부 또는 경계
                    foundRoomId = room.id;
                    break; // 하나만 찾으면 종료
                }
            }

            return foundRoomId;

             // 포함된 방 없음
        }catch (Exception e){
            return null;
        }

    }


    class CoordinateData {
        double x;
        double y;
    }

    class Room {
        String id;
        String name;
        List<CoordinateData> image_path;
    }

    class RobotPosition {
        double x;
        double y;
    }

    class ChargingStation {
        RobotPosition image_position;
    }

    public class RoomListJson {
        List<Room> room_list;
        ChargingStation charging_station;
        public Map<String, List<String>> assign_info;
    }

}
