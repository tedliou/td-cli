from PyInstaller.utils.hooks import collect_all
import os

datas, binaries, hiddenimports = collect_all("td_cli")
root = os.path.abspath(os.path.join(SPECPATH, ".."))
a = Analysis([os.path.join(root, "src/td_cli/cli.py")], pathex=[os.path.join(root, "src")], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
# onedir: td starts on every Command, so skip the onefile extraction of the runtime each time.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="td", console=True, contents_directory="td-runtime")
coll = COLLECT(exe, a.binaries, a.datas, name="td")
