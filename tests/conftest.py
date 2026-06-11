import pytest


@pytest.fixture
def sample_chart_data_normal():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 99.1, "previous_value": 98.8}]
            }
        ]
    }


@pytest.fixture
def sample_chart_data_threshold_breach():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 95.2, "previous_value": 98.5}]
            }
        ]
    }


@pytest.fixture
def sample_chart_data_no_history():
    return {
        "result": [
            {
                "data": [{"metric": "success_rate", "value": 99.1}]
            }
        ]
    }
