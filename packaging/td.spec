from PyInstaller.utils.hooks import collect_all
import os

datas, binaries, hiddenimports = collect_all("td_cli")
root = os.path.abspath(os.path.join(SPECPATH, ".."))
a = Analysis([os.path.join(root, "src/td_cli/cli.py")], pathex=[os.path.join(root, "src")], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="td", console=True)
