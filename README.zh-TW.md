# notes-client-mcp

**Language:** [English](README.md) | 繁體中文

透過 backend `NotesSession` COM 自動化，讓 MCP 存取本機的 HCL Notes Client（不使用
`NotesUIWorkspace`——驅動畫面上已開啟的 client 曾經導致 NLNOTES.exe 崩潰，詳見 plan 文件）。

單一 process，由你的 MCP client（Claude Desktop/Code、GitHub Copilot……）直接用 stdio 啟動：
同一個 process 裡同時持有 COM session、也講 MCP。不需要另外啟動 server、不用管理 port、不需要
Docker——就是像 `mcp-server-git` 那樣單純的 `command`/`args` 設定。

## 需求

- Windows，已安裝並設定好 HCL/IBM Notes Client（ID 檔已就緒、`notes.ini` 可被解析）。
- **一個位元數對齊你安裝的 Notes Client 的 Python 直譯器。**這是唯一真正需要花點功夫設定的地方，
  沒有辦法繞過——COM 自動化要求呼叫端的位元數要跟已註冊的 COM server 位元數一致。判斷你需要哪一種：

  1. 檢查登錄檔：
     - 存在 `HKLM\SOFTWARE\WOW6432Node\Lotus\Notes` → 你的 Notes Client 是 **32-bit**，需要
       32-bit Python。
     - 存在 `HKLM\SOFTWARE\Lotus\Notes`（且沒有上面那個 WOW6432Node 版本）→ 你的 Notes Client
       是 **64-bit**，需要 64-bit Python。
     - （大部分 12.0.2 / 14.x 之後的 Notes 安裝都只有 64-bit；比較舊的安裝就算在 64-bit
       Windows 上也常常是 32-bit。）
  2. 如果沒有對應位元數的 Python，取得一個：
     - 到 [python.org/downloads/windows](https://www.python.org/downloads/windows/) 下載——
       頁面上有分開的 32-bit（「Windows installer (x86)」）跟 64-bit（「Windows installer
       (x86-64)」）安裝檔，選對應的那個裝。
     - 或者如果你用 [pyenv-win](https://github.com/pyenv-win/pyenv-win)，32-bit 版本的版號
       會加 `-win32` 後綴（例如 `pyenv install 3.13.1-win32`）。
  3. 驗證：跑 `python -c "import platform; print(platform.architecture())"`，應該要印出跟你
     需要的一致的 `32bit` 或 `64bit`。

- 在這個目錄下，用該直譯器跑 `pip install -e .`。

## 密碼：`.env`（必要）

在跑起來**之前**，先把 `.env.example` 複製成專案根目錄下的 `.env`，設定 `NOTES_PASSWORD`。
**這會把你的 Notes ID 密碼以明文存在磁碟上**——這是刻意選擇「方便優先於安全」的取捨。`.env` 已
加進 `.gitignore`；絕對不要把它提交、分享，或讓它離開這台機器。

沒有互動輸入的 fallback：已經實測確認，由 MCP client 啟動這個 process 時這種提示完全無法運作
（client 會把 stdin 整個拿去跑 JSON-RPC 串流，提示只會永遠卡住，最後被 client 判定連線逾時直接
砍掉），所以不值得留著這段用不到的程式碼。沒設定 `NOTES_PASSWORD` 的話，process 會直接快速失敗，
回報清楚的錯誤訊息。

**密碼永遠只存在於這台機器上。**絕對不要把它放進 Claude Desktop / GitHub Copilot 的 MCP 設定檔裡。

## 註冊進你的 MCP client

Tool 依風險分成四個等級，用對應的指令來選：

| 指令                          | 涵蓋的 tools                                        |
|-------------------------------|------------------------------------------------------|
| `notes-client-mcp`            | 郵件 + 一般文件/view 讀取（預設）                     |
| `notes-client-mcp-design`     | `read` + Form/View/Agent/DXL 設計檢視                 |
| `notes-client-mcp-write`      | `read` + 建立/更新文件、寄信                           |
| `notes-client-mcp-all`        | 全部都有                                              |

```json
{
  "mcpServers": {
    "notes-client": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["-m", "notes_mcp.server"]
    }
  }
}
```

（或者把 `command` 直接指向裝好的其中一個 console script，例如
`notes-client-mcp-design.exe`，取代 `python.exe -m notes_mcp.server`。）

把 `.mcp.json.example` 複製成 `.mcp.json`（已加進 `.gitignore`——裡面會有你本機的 Python
路徑），填入你的直譯器路徑；裡面已經把四個等級都註冊成獨立的 server。預設只要在 MCP client 啟用
`read` 那個就好；真的需要 `write`/`design`/`all` 權限時再自己手動啟用（例如透過 `/mcp`），因為
那是真正的權限升級，不該是預設行為。

## Tools

Read（`read` profile）：
- `get_mail_database_info`、`list_mail_folders`、`search_mail`、`read_mail`
- `get_database_info`、`read_document`、`search_view`（任何資料庫，用 server+file path 指定）

Design（`design` profile，額外新增）：
- `list_forms`、`list_views`（含 selection formula + 欄位公式）、`list_agents`
- `export_design_dxl`——完整匯出 form/view/agent 的 DXL（XML），含 agent 的 LotusScript/公式原始碼。
  需要目標資料庫的 Designer 層級 ACL 權限。

Write（`write` profile，額外新增——**每一個都會先透過 MCP elicitation 跳出互動確認才會真的寫入**，
所以你的 MCP client 需要支援 elicitation 這些 tool 才能正常運作）：
- `create_document(server_name, file_path, form, fields)`——任何資料庫
- `update_document(server_name, file_path, unid, fields)`——任何資料庫
- `send_mail(sendto, subject, body)`——從目前使用者的信箱寄出

## 已知限制

- 如果你的 MCP client 同時註冊多個 profile（例如 `.mcp.json.example` 裡的四個都開），它們的
  `Initialize()` 呼叫在 client 啟動時可能剛好落在同一瞬間，撞上 Notes ID 檔的鎖
  （`"The ID file is locked by another process"`）——已實測重現，並已在 `notes_backend.py` 加上
  短暫的 retry-with-backoff 修好（這個鎖只在單次 `Initialize()` 呼叫期間存在，不是整個 session
  都鎖著，所以稍等一下重試就會成功）。已做壓力測試，4 個 profile 同時啟動 12/12 次都成功連線；如果
  你之後還是遇到這個錯誤，值得重新測一次，不要假設它是永久性的問題。
- 沒有通用的「列出所有資料庫」功能——只有信箱資料庫會自動偵測（透過 `notes.ini` 的
  `MailServer`/`MailFile`）。其他資料庫要自己明確指定 `server` + `file_path`。
- 目前還沒有行事曆相關的 tool。
- 寫入類 tool 已經過程式碼審查與 import 層級測試，但**還沒有真的對活的資料庫/信箱跑過**——真的跑
  下去會建立真實文件/寄出真實郵件。請自己先在一份測試文件或自己的信箱上驗證過，再信任它們去動真正
  重要的資料。
- `export_design_dxl` 使用 `session.CreateDXLExporter(nc).Export()`——其他看起來合理的呼叫方式
  （`.SetInput()`、`.Input =`）都試過，在這個 Domino 版本的 COM binding 上不存在；如果未來某個
  Domino 版本又不一樣了，`tools/design.py` 的 `_export_dxl()` 已經會依序嘗試四種寫法，全部失敗才會
  回報錯誤。
- 這個工具本質上綁定 Windows、綁定安裝 Notes 的那台機器——沒有伺服器模式，也沒有跨機器的用法。

## 煙霧測試（Smoke test）

```
C:\path\to\python.exe -m notes_mcp.server
```
確認它印出 `connected as '...'`。接著把它註冊進你的 MCP client（上面的步驟），請它呼叫
`get_mail_database_info` / `search_mail` 帶一個真實查詢，確認結果跟你的信箱相符。
