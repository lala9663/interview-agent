"""블라인드 필터: 입력에서 지우고, 출력에 금지 항목이 없게 한다."""

from __future__ import annotations

import pytest

from app.blind.filter import BlindFilter
from app.blind.rules import BlindRules
from tests.conftest import INTERNAL

PROFILE = {
    "education": [{"school": "한국대학교", "major": "전자공학", "degree": "학사"}],
    "gender": "F",
    "birthDate": "2001-03-02",
    "essays": [{"title": "성장 과정", "text": "아버지는 공무원이셨고 어머니는 교사였습니다. 저는 동아리에서 펌웨어를 개발했습니다."}],
    "projects": [{"title": "센서 노드", "text": "참여자가 다섯 명인 팀에서 지원자가 펌웨어를 맡았습니다."}],
    "extra": {"family": "2남 1녀 중 장녀", "hobby": "등산", "religion": "없음"},
}


def test_profile_keys_removed_and_sentences_masked(blind: BlindFilter):
    filtered, excluded = blind.filter_profile(PROFILE)
    assert "gender" not in filtered and "birthDate" not in filtered
    assert "family" not in filtered["extra"] and "religion" not in filtered["extra"]
    assert filtered["extra"]["hobby"] == "등산"
    essay = filtered["essays"][0]["text"]
    assert "아버지" not in essay and "어머니" not in essay and "[블라인드 처리됨]" in essay
    assert "펌웨어를 개발했습니다" in essay  # 금지 항목이 없는 문장은 남는다
    assert set(excluded) == {"gender", "age", "family", "religion"}
    assert blind.check_obj(filtered) == []
    assert PROFILE["gender"] == "F"  # 원본은 바꾸지 않는다


@pytest.mark.parametrize("text,topic", [
    ("결혼 계획이 있으신가요?", "marriage"), ("고향이 어디세요?", "origin"), ("올해 나이가 어떻게 되세요?", "age"),
    ("부모님은 어떤 일을 하시나요?", "family"), ("다니는 교회가 있나요?", "religion"),
    ("지지하는 정당이 있습니까?", "politics"), ("키가 몇이에요?", "physical"), ("집은 전세로 사시나요?", "property"),
    ("여자라서 야근이 힘들지 않겠어요?", "gender"), ("95년생이시죠?", "age"),
])
def test_forbidden_questions_are_detected(blind: BlindFilter, text: str, topic: str):
    assert topic in {v.topic for v in blind.check_text(text)}


@pytest.mark.parametrize("text", [
    "가족 같은 분위기의 팀에서 일했습니다.", "참여자가 많은 프로젝트였습니다.", "지식재산권 관련 교육을 들었습니다.",
    "원인을 알아내는 데 이틀이 걸렸습니다.", "전세계 사용자를 대상으로 했습니다.", "특성별로 나눠 분석했습니다.",
    "지원자가 직접 설계했습니다.", "다국적 팀과 협업했습니다.", "RTOS 환경에서 태스크 간 동기화는 어떻게 처리하나요?",
])
def test_ordinary_job_talk_is_clean(blind: BlindFilter, text: str):
    assert blind.check_text(text) == []


def test_mask_and_scrub(blind: BlindFilter):
    masked, codes = blind.mask_sentences("팀장으로 일했습니다. 결혼 후에도 계속 일할 생각입니다. 감사합니다.")
    assert masked == "팀장으로 일했습니다. [블라인드 처리됨] 감사합니다." and codes == ["marriage"]
    out, codes = blind.scrub({"a": ["고향은 부산입니다.", "성실합니다."], "n": 3})
    assert out == {"a": ["[블라인드 처리됨]", "성실합니다."], "n": 3} and codes == ["origin"]
    assert blind.check_obj(out) == []


def test_rules_come_from_yaml_only(tmp_path):
    """현업 기준이 바뀌면 YAML만 고친다: 새 주제를 더하면 코드 수정 없이 걸러진다."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        'version: "t1"\ntopics:\n  - code: military\n    label: 병역\n    profile_keys: [military]\n'
        '    keywords: [군 복무, 병역]\n    patterns: ["(?<![가-힣])군대"]\n', encoding="utf-8")
    custom = BlindFilter(BlindRules.load(path))
    assert [v.topic for v in custom.check_text("군대는 다녀오셨나요?")] == ["military"]
    assert custom.check_text("결혼하셨나요?") == []  # 이 파일에 없는 주제는 보지 않는다
    assert custom.filter_profile({"military": "필", "name": "x"}) == ({"name": "x"}, ["military"])
    path.write_text("topics:\n  - code: x\n    label: y\n    patterns: ['(']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="정규식 오류"):
        BlindRules.load(path)


def test_blind_check_endpoint(client):
    r = client.post("/internal/v1/blind/check", headers=INTERNAL,
                    json={"texts": ["캡스톤에서 맡은 역할은?", "결혼은 하셨어요? 자녀 계획은요?"]})
    assert r.status_code == 200
    body = r.json()
    assert body["clean"] is False and body["rulesVersion"]
    assert body["results"][0] == {"index": 0, "clean": True, "violations": []}
    second = body["results"][1]
    assert second["clean"] is False
    assert {"topic": "marriage", "label": "결혼·출산", "matched": "결혼", "ruleType": "keyword"} in second["violations"]
