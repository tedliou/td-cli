# 授權與第三方聲明

規格：[GitHub issue #135](https://github.com/tedliou/td-cli/issues/135)。

本專案自有程式採 MIT，著作權人為 Ted Liou（2026）。第三方元件維持各自授權，
TouchDesigner 本體不在本專案 MIT 授權範圍內。

## 設計

- 根目錄 `LICENSE` 保存標準 MIT；`pyproject.toml` 使用 SPDX `MIT` 與 `license-files`。
- `THIRD_PARTY_NOTICES.md` 記錄版本、來源、授權原文位置及 MPL 原始碼取得方式。
- `LICENSES/` 保存上游原文與 inventory。清單涵蓋鎖定工具鏈，不宣稱每一元件都被每個產物載入。
- 檢查套件內嵌聲明、setuptools vendor、pydantic-core SBOM 與 Python 原生依賴，
  避免只列直接依賴。鎖檔或原文改變時，驗證須要求同步更新清單。
- 四個 ZIP 均附相同授權集合；wheel／sdist 亦附授權文件。安裝器驗證巢狀檔案。
- 不變更版本、Protocol、Agent、升級 migration，不回寫歷史 Release，也不發版。

獨立設計審查確認：既有打包 inventory 含 setuptools vendor、OpenSSL、SQLite，
pydantic-core wheel 提供 Rust SBOM；既有安裝驗證只檢查根目錄，需涵蓋巢狀授權文件。

## 維護

依賴或 Python runtime 更新時，重新核對實際 PyInstaller inventory 與上游套件，
保留原始著作權、LICENSE、COPYING、NOTICE 及必要的 vendored 聲明。
MPL 元件若有修改，必須更新對應原始碼取得方式，不能繼續宣稱是未修改的上游版本。
上游來源、精確版本及原文 SHA-256 應隨清單更新，不可只更新檢查碼來略過審查。

## 依據

- [MIT 標準全文](https://opensource.org/license/mit)
- [Mozilla MPL 2.0 FAQ，原始碼取得與 larger work](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)
- [PyPA 的 license 與 license-files](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license-and-license-files)
- [PyInstaller 授權例外](https://pyinstaller.org/en/v6.15.0/license.html)
