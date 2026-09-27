import careflow_rules


def test_package_exports_the_engine() -> None:
    assert careflow_rules.RULESET_VERSION == "2026.3.0"
    assert careflow_rules.__version__ == careflow_rules.RULESET_VERSION
    assert callable(careflow_rules.evaluate)
