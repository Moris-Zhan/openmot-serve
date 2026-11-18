from tests.test_compose_tensorrt import BASE, OVERRIDE, WEIGHTS, needs_docker, override, rendered

STACK = [BASE, WEIGHTS, OVERRIDE]


def _without_precision(environment: dict) -> dict:
    return {key: value for key, value in environment.items() if key != "OPENMOT_PRECISION"}


def test_the_override_takes_the_precision_from_the_environment_and_defaults_to_fp16() -> None:
    assert override()["services"]["worker"]["environment"]["OPENMOT_PRECISION"] == "${OPENMOT_PRECISION:-fp16}"


@needs_docker
def test_the_precision_is_fp16_when_it_is_not_set() -> None:
    assert rendered(STACK)["services"]["worker"]["environment"]["OPENMOT_PRECISION"] == "fp16"


@needs_docker
def test_the_precision_follows_the_environment_variable() -> None:
    worker = rendered(STACK, OPENMOT_PRECISION="int8")["services"]["worker"]
    assert worker["environment"]["OPENMOT_PRECISION"] == "int8"
    assert worker["environment"]["OPENMOT_BACKEND"] == "tensorrt"


@needs_docker
def test_only_the_worker_precision_differs_between_the_two_renders() -> None:
    default = rendered(STACK)["services"]
    int8 = rendered(STACK, OPENMOT_PRECISION="int8")["services"]
    assert int8["api"] == default["api"]
    assert int8["redis"] == default["redis"]
    assert _without_precision(int8["worker"]["environment"]) == _without_precision(default["worker"]["environment"])
    assert {key: value for key, value in int8["worker"].items() if key != "environment"} == {
        key: value for key, value in default["worker"].items() if key != "environment"
    }
