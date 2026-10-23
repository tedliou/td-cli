from pathlib import Path


def test_build_graph_has_one_spec_per_public_executable() -> None:
    for name in ("td", "td-daemon", "td-agent"):
        text = Path(f"packaging/{name}.spec").read_text(encoding="utf-8")
        assert f'name="{name}"' in text
        assert "EXE(" in text
        # Only td, started for every Command, is onedir; the long-lived tools stay onefile.
        assert ("COLLECT(" in text) == (name == "td")
    assert 'contents_directory="td-runtime"' in Path("packaging/td.spec").read_text(
        encoding="utf-8"
    )


def test_build_graph_pins_reproducibility_inputs() -> None:
    text = Path("scripts/build_release.py").read_text(encoding="utf-8")
    assert '"PYTHONHASHSEED": "0"' in text
    assert '"SOURCE_DATE_EPOCH": str(args.source_epoch)' in text
