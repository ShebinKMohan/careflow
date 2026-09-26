import careflow_api


def test_package_imports() -> None:
    assert careflow_api.__version__ == "0.0.0"
