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
| `notes-client-mcp-write`      | `read` + 建立/更新文件                                |
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
路徑），填入你的直譯器路徑；裡面已經把四個等級都註冊成獨立的 server，每一個都是完全獨立的
process、各自連一個 Notes session。

**同一時間只該開一個，這四個是階梯關係，不是可以疊加的加購項目。**每個 profile 都已經包含
`read` 的 tools（`write` = read+write，`all` = 全部），所以同時開兩個以上完全沒有意義——比較
寬的那個早就涵蓋比較窄的了，多開的只是多付出一次多餘的 Notes 登入成本，沒有任何額外好處。預設只有
`read` 會在 MCP client 啟用；真的需要更多權限時，啟用你要的那一個（例如 `write`）並把 `read`
關掉，而不是兩個都開著，因為那是真正的權限升級，不該是預設行為。

## Tools

Read（`read` profile）：
- `get_mail_database_info`——從 `notes.ini` 解析出目前使用者信箱資料庫的 server + file path。
  沒有其他信箱專用的 tool 了：拿到這個之後，跟操作任何其他資料庫一樣用下面的通用 tool 就好（原本有
  `list_mail_folders`/`search_mail`/`read_mail` 這幾個包裝，因為通用 tool 配上信箱的 server+path
  就能做一樣的事，屬於多餘的特殊化，已移除）。
- `get_database_info`、`read_document`、`search_view`（任何資料庫，用 server+file path 指定）
- `export_view_csv`——直接把 view 的資料寫成本機 CSV 檔（回傳的是檔案路徑，不是資料本身），不像
  `search_view` 會受限於 MCP tool 回傳結果的大小上限，適合資料量大的 view。
- `find_document_by_key`——用 view 排序過的欄位快速查找（走 view 索引）。只要你要查的值本來就是
  某個既有 view 的欄位，優先用這個，而不是 `search_database`。
- `search_database`——用 Notes `@formula` 搜尋整個資料庫的所有文件，適合沒有合適 view 可用的情境。
  比 `search_view`/`find_document_by_key` 慢很多，因為沒有走 view 索引；`max_docs` 會限制回傳筆數
  （已實測確認：底層 `NotesDatabase.Search` 呼叫本身的 `maxdocs` 參數只會限制 `.Count` 回報的數字，
  並不會限制實際能走訪到的文件數——這個 tool 自己在迴圈裡強制做上限，不依賴那個參數）。

Design（`design` profile，額外新增）：
- `list_forms`、`list_views`（含 selection formula + 欄位公式）、`list_agents`
- `list_design_elements(server_name, file_path, kind)`——列出沒有專屬 tool 的設計元素種類，用名稱
  列表：`subforms`、`outlines`、`pages`、`framesets`、`script_libraries`、`shared_fields`、
  `database_script`、`navigators`、`image_resources`、`java_resources`、`stylesheet_resources`、
  `data_connections`、`replication_formulas`、`profiles`、`folders`、`acl`、`icon`、`help_about`、
  `help_using`（每一個都已對著真實資料庫實測過——有幾個看起來合理的候選，例如把 `shared_actions`
  當成獨立 kind、composite applications/components、web pages、XSLTs，在這個 Domino 版本的
  `NotesNoteCollection` API 上不存在，故意沒放進去，而不是放進去卻悄悄壞掉）。`actions`（shared
  actions）是單一一筆彙總 note，沒辦法用這個方式逐筆列出——要看內容請用
  `export_design_dxl(kinds=["actions"])`。
- `get_database_settings(server_name, file_path)`——分類、design template 名稱、replica ID、
  配額/使用率、managers、document/design locking、multi-db search、address book 旗標、
  pending-delete 狀態。
- `list_acl(server_name, file_path)`——資料庫定義的角色，以及每個 entry 的名稱/存取等級（標準
  Domino 0-6 分級，附可讀名稱）/角色/幾個常見的能力旗標。
- `export_design_dxl(server_name, file_path, kinds, name_filter)`——完整匯出上面任何種類的 DXL
  （XML）（預設 `["forms", "views", "agents"]`），含 agent 的 LotusScript/公式原始碼、form/view
  公式，以及 `list_design_elements` 沒辦法逐筆列出的種類（例如 `actions`）的完整內容。需要目標
  資料庫的 Designer 層級 ACL 權限。

Write（`write` profile，額外新增——**每一個都會先透過 MCP elicitation 跳出互動確認才會真的寫入**，
所以你的 MCP client 需要支援 elicitation 這些 tool 才能正常運作）：

`create_document`/`update_document` 的 `fields` 參數是 `{名稱: 值}`，用一般 JSON 型別即可——
`str`/`int`/`float`/`bool`/list 都能正確對應到正確的 Notes 欄位型別（已實測確認）。唯一的例外是
日期：純字串不會自動變成真正的 Date/Time 欄位（只會存成文字），所以會自動偵測 ISO-8601 格式的字串
（例如 `"2026-03-05"` 或完整日期時間）並轉成真正的 Notes 日期值——所以如果某個欄位本來就是要存
「看起來像日期的純文字」，要注意這個自動轉換行為。

- `create_document(server_name, file_path, form, fields)`——任何資料庫。設定欄位**前後**各呼叫
  一次 `ComputeWithForm`（先讓預設值公式跑完，再讓依賴你所設欄位值的公式重新計算），比照真實
  LotusScript 寫法，而不是單純寫欄位——已實測確認：只在設完欄位後呼叫一次，會漏掉表單只在「建立
  當下」才會計算的欄位。
- `update_document(server_name, file_path, unid, fields)`——任何資料庫。**絕不會覆蓋掉別人同時
  做的修改**：已實測確認 backend 的 `Save()` 本身**不會**偵測或拒絕過期的寫入（兩個各自獨立、指向
  同一份文件的記憶體副本都能成功 `Save`，第二次會悄悄蓋掉第一次、沒有任何錯誤——那種保護是前端
  `NotesUIDocument` 才有的行為，這個 backend class 沒有），所以衝突偵測是手動做的（存檔前比對
  `LastModified`）；偵測到衝突時會重新讀取目前的文件、把你要的 `fields` 重新套用一次，而不是硬存，
  如果重試一次還是撞到衝突就會回報清楚的錯誤。也會檢查 Document Locking（資料庫層級的功能，大部分
  資料庫預設沒開——只有目標資料庫真的有開這個功能時才會生效），如果文件被別人鎖住會直接拒絕並回報
  清楚的錯誤。

沒有 `send_mail`——已移除，因為它是不必要的特例；寄信跟這個工具其他沒有特別特殊化的寫入操作本質上
沒有差別。

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
- 寫入類 tool 已經透過真正的 MCP 路徑、帶著 elicitation 確認，對活的資料庫親自測過（建立、更新、
  衝突偵測、欄位型別處理）——不過每個資料庫的表單/ACL 都不一樣，真的要動重要資料前，還是建議先拿
  自己的測試文件試一次。
- `export_design_dxl` 使用 `session.CreateDXLExporter(nc).Export()`——其他看起來合理的呼叫方式
  （`.SetInput()`、`.Input =`）都試過，在這個 Domino 版本的 COM binding 上不存在；如果未來某個
  Domino 版本又不一樣了，`tools/design.py` 的 `_export_dxl()` 已經會依序嘗試四種寫法，全部失敗才會
  回報錯誤。
- 這個工具本質上綁定 Windows、綁定安裝 Notes 的那台機器——沒有伺服器模式，也沒有跨機器的用法。

## 煙霧測試（Smoke test）

```
C:\path\to\python.exe -m notes_mcp.server
```
確認它印出 `connected as '...'`。接著把它註冊進你的 MCP client（上面的步驟），請它先呼叫
`get_mail_database_info`，再對結果裡的 `($Inbox)` view 呼叫 `search_view`，確認結果跟你的信箱相符。
