from pathlib import Path


def test_build_graph_has_one_onefile_spec_per_public_executable() -> None:
    for name in ("td", "td-daemon", "td-agent"):
        text = Path(f"packaging/{name}.spec").read_text(encoding="utf-8")
        assert f'name="{name}"' in text
        assert "EXE(" in text
        assert "COLLECT(" not in text


def test_build_graph_pins_reproducibility_inputs() -> None:
    text = Path("scripts/build_release.py").read_text(encoding="utf-8")
    assert '"PYTHONHASHSEED": "0"' in text
    assert '"SOURCE_DATE_EPOCH": str(args.source_epoch)' in text
