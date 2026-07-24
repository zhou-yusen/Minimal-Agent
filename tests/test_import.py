def test_package_imports_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import minimal_agent

    assert minimal_agent.__version__ == "0.1.0"
