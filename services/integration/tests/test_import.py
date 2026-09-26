import careflow_integration


def test_package_imports() -> None:
    assert careflow_integration.__version__ == "0.1.0"
