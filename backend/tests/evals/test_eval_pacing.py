"""make eval(private_rag_apps.evals.__main__)のVoyage呼び出しペーシングのテスト。

ADR 0003 / task-T4補足コンテキスト#3 で指摘されている通り、evals/__main__.py は
Voyage呼び出し(retrieve_context経由のembed+rerank)に対するペーシング機構を持たず、
無支払い枠(3RPM)のレート制限で完走できないことが既知の問題だった。
ingestion/indexer.py::_pace_embed_call と同じ方式(呼び出し間隔がsettings設定値未満に
ならないよう待機)を、アイテムのループ内(retrieve_context呼び出しの前)に追加する。
"""

import private_rag_apps.evals.__main__ as main_module


def test_pace_voyage_call_waits_at_least_min_interval_apart(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_last_voyage_call_at", None)

    fake_now = [1000.0]
    sleep_calls: list[float] = []

    def fake_monotonic() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(main_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(main_module.settings, "ingest_embed_min_interval_sec", 21.0)

    main_module._pace_voyage_call()
    assert sleep_calls == []  # 初回は待機しない

    fake_now[0] += 5.0  # 5秒しか経過していない
    main_module._pace_voyage_call()
    assert sleep_calls == [16.0]  # 21 - 5 = 16秒待つ

    fake_now[0] += 21.0  # 十分な間隔が空いた
    main_module._pace_voyage_call()
    assert sleep_calls == [16.0]  # 追加の待機は発生しない
