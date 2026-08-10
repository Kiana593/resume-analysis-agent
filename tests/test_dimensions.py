"""七维定义测试 — 零外部依赖。"""
from src.core.dimensions import (
    CATEGORY_TO_DIM,
    DEFAULT_WEIGHTS,
    DIMENSION_KEYS,
    DIM_LABELS,
)


class TestDimensionKeys:
    def test_seven_dimensions_in_order(self):
        assert len(DIMENSION_KEYS) == 7
        assert DIMENSION_KEYS == (
            "knowledge",
            "skill",
            "qualifications",
            "preference",
            "motivation",
            "trait",
            "self_concept",
        )

    def test_category_map_covers_all_categories(self):
        assert set(CATEGORY_TO_DIM) == {
            "知识", "技术", "任职条件", "招聘偏好", "动机", "特质", "自我概念",
        }
        assert set(CATEGORY_TO_DIM.values()) == set(DIMENSION_KEYS)

    def test_weights_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
        assert set(DEFAULT_WEIGHTS) == set(DIMENSION_KEYS)

    def test_labels_cover_all_dimensions(self):
        assert set(DIM_LABELS) == set(DIMENSION_KEYS)
        assert DIM_LABELS["knowledge"] == "知识"
        assert DIM_LABELS["skill"] == "技术"
        assert DIM_LABELS["qualifications"] == "任职条件"
        assert DIM_LABELS["preference"] == "招聘偏好"
