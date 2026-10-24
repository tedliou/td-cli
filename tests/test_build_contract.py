from pathlib import Path


def test_build_graph_has_one_spec_per_python_executable() -> None:
    for name in ("td-daemon", "td-agent"):
        text = Path(f"packaging/{name}.spec").read_text(encoding="utf-8")
        assert f'name="{name}"' in text
        assert "EXE(" in text
        # onedir, each with its own runtime directory: the two share one install directory.
        assert "COLLECT(" in text
        assert f'contents_directory="{name}-runtime"' in text
    # td is the native Rust client; no PyInstaller spec builds it any more.
    assert not Path("packaging/td.spec").exists()


def test_build_graph_pins_reproducibility_inputs() -> None:
    text = Path("scripts/build_release.py").read_text(encoding="utf-8")
    assert '"PYTHONHASHSEED": "0"' in text
    assert '"SOURCE_DATE_EPOCH": str(args.source_epoch)' in text
    assert '["cargo", "build", "--release", "--locked"]' in text
    assert "--remap-path-prefix" in text
