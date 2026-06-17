package com.skmagic.push.mapper;

import org.apache.ibatis.annotations.Mapper;

import java.util.List;

/**
 * serial → 건물 멤버(loginId) 매핑 조회 매퍼.
 *
 * <p>backend-api 의 {@code BuildingMember.xml getBuildingMemberListBySerial} 와 동일한 조인
 * ({@code device → user_building → user_building_member → user}, {@code linked_yn = 'Y'}) 을 사용한다.
 * 토픽에는 serial 만 있으므로 이 매핑을 통해 push 대상 사용자(loginId)를 찾는다.
 */
@Mapper
public interface BuildingMemberMapper {

    /** 해당 serial 디바이스가 속한 건물의 멤버 loginId 목록을 반환한다. */
    List<String> getBuildingMemberLoginIdListBySerial(String serial);
}
