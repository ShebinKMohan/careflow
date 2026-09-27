from voice_agent.metrics import MetricsRegistry


def test_metrics_registry_counts_turns_and_failures_by_outcome() -> None:
    registry = MetricsRegistry()
    registry.record_turn("success")
    registry.record_turn("timeout")
    registry.record_turn("success")
    registry.record_turn("error")

    output = registry.render(active_media_sessions=2)

    assert "voice_agent_active_media_sessions 2" in output
    assert "voice_agent_turns_total 4" in output
    assert 'voice_agent_turn_failures_total{outcome="timeout"} 1' in output
    assert 'voice_agent_turn_failures_total{outcome="error"} 1' in output


def test_metrics_registry_render_is_prometheus_text() -> None:
    registry = MetricsRegistry()
    output = registry.render(active_media_sessions=0)
    assert output.startswith("# HELP")
    assert "# TYPE voice_agent_turns_total counter" in output
    assert "# TYPE voice_agent_active_media_sessions gauge" in output
    assert output.endswith("\n")


def test_metrics_registry_escapes_label_values() -> None:
    registry = MetricsRegistry()
    registry.record_turn('weird"outcome')
    output = registry.render(active_media_sessions=0)
    assert 'outcome="weird\\"outcome"' in output
