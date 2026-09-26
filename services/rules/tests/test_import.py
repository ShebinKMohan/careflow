import careflow_rules


def test_package_imports() -> None:
    assert careflow_rules.__version__ == "0.0.0"
