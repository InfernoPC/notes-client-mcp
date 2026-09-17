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
- `get_database_by_replica_id`——把 replica ID 換成其他 tool 都需要的 server + file path。
  跨資料庫的連結本來就不存路徑：outline 的 `<databaselink database='4825666C0023AB44'/>`、
  文件連結（小黃紙）、`notes://server/<16 碼 hex>/...` 這種 URL、複本內容（Replication）
  屬性對話框，給的都只有 replica ID，而其他每個 tool 走的 `session.GetDatabase` 只吃路徑——
  所以以前拿到 replica ID 等於死路一條，只能用猜的。兩種寫法都接受（`48257B98001E8842` 或
  `48257B98:001E8842`）。replica ID 標示的是一組「複本」而不是位置，所以查詢一次只針對一台
  server（`server_name` 預設 `""`，也就是本機 data 目錄）；那台 server 上沒有這個複本時回傳
  null 而不是錯誤，所以一台一台試是正常用法，不是一連串失敗。目前使用者沒有權限開的複本，跟
  根本不存在的複本無法區分，都是回 null。這是掃目錄不是查索引，所以拿到 `file_path` 之後就
  改用路徑。
- `extract_document_media`——`read_document` 回傳的欄位值一律是純文字，就算是 rich text 欄位也一樣
  （`read_document` 的結果會用 `rich_text_items` 標出哪些欄位是 rich text；也可以呼叫
  `read_document` 時帶 `include_media=True`，一次拿到兩者，不用分開呼叫——反正不管哪種呼叫方式都要
  做一次 DXL 匯出，如果還不確定會不會需要圖片/附件，就先不要開這個選項，走一般欄位讀取就好）。貼上去的圖片（例如
  截圖）跟真正的檔案附件/OLE 物件是兩種完全不同的東西，用兩種不同方式抓取（已實測確認：兩種抓取方式
  互相抓不到對方的內容）：附件/OLE 物件是每個欄位自己的 `EmbeddedObjects`（`.ExtractFile`）；貼上
  去的圖片是原始的 Notes 點陣圖 CD 記錄，本身根本不是「embedded object」，唯一能拿到的方式是對整份
  文件做 DXL 匯出，並設定 `NotesDXLExporter.ConvertNotesBitmapsToGIF = True`，再解碼匯出結果裡的
  `<gif>`/`<jpeg>` base64 區塊（跳過 `<gif originalformat='notesbitmap'>` 這種——那是自動產生的附件
  縮圖，不是真正的圖片；解碼後小於 4KB 的也跳過，幾乎都是縮圖）。檔案會寫進本機一個依文件產生的暫存
  資料夾，回傳的是檔案路徑——圖片結果可以直接用 Read 工具打開來看。
- `extract_document_tables`——rich text 表格的結構（列/欄、合併儲存格、底色）在 `read_document`
  的純文字欄位值裡完全消失（每個儲存格的文字全部黏在一起）。跟圖片不一樣，表格不需要特殊處理——它們
  在 DXL 匯出結果裡本來就是結構化的元素（`<table>`/`<tablerow>`/`<tablecell>`），直接解析就好
  （已對一份含多個表格的真實文件實測確認，包含有合併儲存格/底色的表格）。回傳的是攤平的清單
  `{item_name, table_index, rows, row_labels}`，因為同一個欄位可能有不只一個表格；`rows` 裡每個
  儲存格是 `{text, colspan, rowspan, bgcolor}`（colspan/rowspan 預設 1，bgcolor 預設 null）——
  這是 DXL 原本編碼的每列儲存格清單，不是重建過的視覺網格，但 colspan/rowspan/bgcolor 已經足夠自己
  手動排出實際版面；`row_labels` 帶的是每一列的 `tablabel` 屬性（常見於分頁式表格），沒有的話是
  null。`read_document` 帶 `include_tables=True` 可以一次拿到同樣的結果，不用分開呼叫。
- `get_view_info`——直接從索引讀出 view 的資料筆數、欄位與 selection formula，不走訪任何一列。
  對還不確定規模的 view，先用這個再決定要不要 `search_view` / `export_view_csv`：那兩個每一列
  都要一次 COM 來回，大 view 可能要跑好幾分鐘，而所有 Notes 呼叫共用同一條 STA thread，一個
  走訪太久就會讓其他 tool call 全部排在後面（已在 63 GB 的 NSF 上實測：一個 35,718 筆的 view
  匯出跑了 20 分鐘還沒結束，同一個 view 用 `get_view_info` 是瞬間回覆）。`entry_count` 是這個
  view 的**文件數**——已用實際走訪比對過：分類標題列不算在內，而且它**不是**走訪筆數的上限，
  因為分類欄位是多值時，同一份文件會掛在每一個值底下、走訪時就被走到好幾次（實測：一個
  `entry_count` 回報 11 的 view，走訪出來是 21 列，對應的仍是那 11 份文件）。請把它當成
  「決定這個 view 要怎麼讀」的規模估計，而不是精確筆數。`is_large` 標示的是建議改用
  `limit`/`skip` 分頁、或用 `category` 縮小範圍的 view。
- `export_view_csv`——直接把 view 的資料寫成本機 CSV 檔（回傳的是檔案路徑，不是資料本身），不像
  `search_view` 會受限於 MCP tool 回傳結果的大小上限，適合資料量大的 view。
- `find_document_by_key`——用 view 排序過的欄位快速查找（走 view 索引）。只要你要查的值本來就是
  某個既有 view 的欄位，優先用這個，而不是 `search_database`。
- `search_database`——用 Notes `@formula` 搜尋整個資料庫的所有文件，適合沒有合適 view 可用的情境。
  比 `search_view`/`find_document_by_key` 慢很多，因為沒有走 view 索引；`max_docs` 會限制回傳筆數
  （已實測確認：底層 `NotesDatabase.Search` 呼叫本身的 `maxdocs` 參數只會限制 `.Count` 回報的數字，
  並不會限制實際能走訪到的文件數——這個 tool 自己在迴圈裡強制做上限，不依賴那個參數）。
  帶 `count_only=True` 則只回 `{"count": N}`，直接從搜尋集合取數，完全不開任何一份文件；它會
  刻意忽略 `max_docs`，因為被截斷的計數不是「比較便宜的答案」，而是錯的答案。

### 只要你真正需要的那一點（issue #6）

Notes 文件不管你想不想要，一份就是 200 多個欄位——整串 `$UpdatedBy`／`$Revisions` 稽核紀錄、
表格每一列一長串字串——而按鈕的 click 程式碼又是一個 Notes 應用系統最肥的地方。所以以前一個很窄的
問題，預設答案卻大得離譜：在六個生產 NSF 上實測，「每個月幾張領料單」回來的是 319,206 字的完整
文件，「這支副表單有哪些按鈕」是 92,882 字的 LotusScript。而在 2.53 GiB 的 NSF 上，光是「數張數」
這個版本就直接把 server 弄掛了——走訪 GiB 級的文件會把唯一那條 STA thread 佔死（見下面的說明）。

上面每一種現在都有一個「窄版」問法：

- `search_database(..., count_only=True)` → `{"count": N}`，一份文件都不開。若剛好有合適的分類
  view，`list_view_categories` 的分類筆數（見下面）能完全走索引回答同一個問題；整個 view 的規模
  則用 `get_view_info` 的 `entry_count`。
- `search_database(..., fields=[...])`、`read_document(..., fields=[...])`、
  `find_document_by_key(..., fields=[...])` 把 `items` 限制在指定欄位。比對不分大小寫
  （`"xflag"` 找得到 `xFlag`），你指定但文件沒有的欄位會列在 `missing_fields`，所以「打錯字」跟
  「欄位是空的」分得出來。
- `list_design_actions(..., include_source=False)` 保留每個事件的語言與 `source_chars`，但不帶
  程式碼本身，讓「有哪些按鈕、誰看得到、哪些有程式碼」變成一個便宜的先導查詢，確認之後再單獨把
  你真正要讀的那一支抓回來。

Design（`design` profile，額外新增）：
- `list_forms`、`list_views`（含 selection formula + 欄位公式）、`list_agents`
- `list_view_categories(server_name, file_path, view_name, max_level)`——分類過的
  view 自己的分類值，不管 view 多大都完全不碰任何文件（已實測確認：
  `NotesViewNavigator.MaxLevel` + `GetNextCategory()` 在任何深度都能正確跳過所有文件）。
  因為分類欄位常常是公式算出來的，不是單純欄位，這裡回傳的是 view 自己算出來、實際拿去分組用的值——
  可以直接拿去餵 `find_document_by_key`，不用自己猜文件裡存的欄位長怎樣。每個分類還會附上
  `descendant_count`（底下總共幾筆）與 `child_count`（只算直屬子項），都是直接從索引項目讀出來的：
  單層分類的文件 view 兩者就是那個分類的文件數，所以「各狀態／各月份／各部門幾張」完全不用讀文件、
  也不用搜尋。多層分類時兩者會不同，而且都不是純粹的文件數——請取最深一層的 `descendant_count`。
  navigator 不提供時是 null（不是 0）。
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
- `list_design_actions(server_name, file_path, name_filter, kinds, include_source)`——符合條件的
  設計元素上每一顆動作按鈕，含 click／hidewhen 程式碼。`include_source=False` 會回傳一樣的結構，
  但不帶程式碼本身（見上面「只要你真正需要的那一點」）。
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

## 安全模型：Profile 是 UX 控制，不是安全沙盒

四個 profile（`read`/`design`/`write`/`all`）只控制 `server.py` 在 MCP 協定層要註冊哪些 tool
名稱。這是給你自己用的「意圖範圍」/便利性控制——防止你（或只是照著現有 tool 操作的 AI 助理）在
只想瀏覽的時候**不小心**碰到寫入操作。

**它不是能擋住 AI agent 本身的安全邊界。** 任何在同一台機器上有 shell 存取權的東西——而驅動這個
專案的 AI coding assistant 通常本來就有——都可以直接 `import notes_mcp.tools.write`，或自己
操作 COM 物件，不管註冊了哪個 profile 都能呼叫 `create_document`/`update_document`。這個
程式碼庫裡沒有任何 runtime 檢查能可靠地擋住這件事，因為會被這種檢查擋住的 agent，同樣也讀得到
原始碼、寫得出不會觸發那個檢查的程式碼。

**唯一不管跑什麼程式碼都真正擋得住的，是 Notes/Domino 自己的 ACL。** 如果拿來連線的 ID 檔案
在某個資料庫上只有 Reader 權限，那不管是透過 MCP tool 呼叫、手寫的繞過腳本，還是任何其他方式，
每一次寫入嘗試都會在伺服器端被拒絕——因為這個拒絕發生在 Notes/Domino 本身內部，不是這個工具的
程式碼在把關。在把 `read` profile 當成保證之前，先用 `list_acl` 確認這個 ID 檔案在資料庫上
實際的存取等級。

如果你是要把這個工具部署給別人用，而且希望「唯讀」的意圖就算 AI agent 誤判或過度主動也真的擋得住，
應該讓對方使用（或幫對方準備）一個在相關資料庫上 ACL 存取等級是 Reader 以下的 ID 檔案——不要只
依賴 `read` profile 的註冊設定。

## 更新檢查（只告知，絕不自動更新）

Server 啟動時會問 `origin` 有沒有更新的 `vX.Y.Z` tag。有的話，就把該版本
**新增／移除／新增參數**的工具**名稱**附加到 MCP `instructions`，讓 AI 能
看見「有個適合這件事的工具，只是現在搆不到」並主動告訴你 —— 而不是自己
手刻一個替代方案繞過去。

不會替你更新任何東西。通知裡直接帶著指令，而「要不要完整重啟」是靠比對
兩份 `pyproject.toml` 的依賴清單決定的：沒變就 `git pull` 加 `/mcp` 重連
即可；有變就必須先完全關閉 Claude Code，因為跑著的 server 正 load 著
pywin32 的 DLL，Windows 會鎖檔讓 pip 裝不上去。

怎麼做到又省又安全：

- **兩段式。** `git ls-remote --tags origin` 不下載任何 object，而且在你已是
  最新版時（也就是絕大多數 session）就只會跑這一段。只有發現更新的 tag 才
  `git fetch` 那一個 tag。
- **只 `fetch`，絕不 `pull`。** fetch 下來的 object 躺在 `.git/` 裡，工作目錄
  完全不動、什麼都不會執行。`pull` 會覆寫這個 process 已經 import 的程式碼，
  導致它回報的版本與實際行為對不上。
- 遠端的工具清單用 `ast.parse` 讀，**絕不 import** —— import 抓下來的程式碼
  去列舉它的工具，等於執行你正想避免信任的那份程式碼。
- 只有識別字會進到 AI 的 context（工具名稱、profile 標籤、參數名稱、點分版本
  號），每一項都經過嚴格 pattern 驗證。遠端的 docstring 與 changelog 文字
  一律不讀，所以 push 不進指令。對照上面的安全模型：這是針對 mirror 被竄改、
  誤 merge 這類較弱情況的廉價防護，擋不住能 push 到 `origin` 的人 —— 那種人
  本來就能讓程式碼在這個 process 裡執行。
- `origin` 寫死；公開 mirror 的信任等級不同，不可混用。
- 結果快取 6 小時、同一個版本只講一次不重複嘮叨，任何失敗（離線、沒有 git、
  逾時、用 wheel 安裝而沒有 `.git`）都靜默略過。設 `NOTES_MCP_UPDATE_CHECK=0`
  可完全關閉。

## 已知限制

- 如果你的 MCP client 同時註冊多個 profile（例如 `.mcp.json.example` 裡的四個都開），它們的
  `Initialize()` 呼叫在 client 啟動時可能剛好落在同一瞬間，撞上 Notes ID 檔的鎖
  （`"The ID file is locked by another process"`）——已實測重現，並已在 `notes_backend.py` 加上
  短暫的 retry-with-backoff 修好（這個鎖只在單次 `Initialize()` 呼叫期間存在，不是整個 session
  都鎖著，所以稍等一下重試就會成功）。已做壓力測試，4 個 profile 同時啟動 12/12 次都成功連線；如果
  你之後還是遇到這個錯誤，值得重新測一次，不要假設它是永久性的問題。
- 沒有通用的「列出所有資料庫」功能——只有信箱資料庫會自動偵測（透過 `notes.ini` 的
  `MailServer`/`MailFile`）。其他資料庫要自己明確指定 `server` + `file_path`，或是用
  `get_database_by_replica_id` 從 replica ID 反查出來（但還是得告訴它要找哪一台 server）。
- 所有 Notes COM 呼叫都序列化在同一條 STA thread 上，而同步的 COM 呼叫沒辦法從外部取消——
  所以一個慢呼叫會擋住後面全部，直到它自己跑完。這件事以前是無聲的：在 63 GB 的 NSF 上實測，
  一個 `export_view_csv` 走訪需要重建索引的 view 跑超過 20 分鐘，期間後續每一個呼叫（連
  `get_database_info` 這種極輕量的都一樣）都卡在佇列裡，最後一律被 MCP client 自己的 idle
  timeout 砍掉，而且沒有任何訊息指出真正的原因。現在還在**排隊中**的呼叫會在
  `NOTES_MCP_QUEUE_TIMEOUT` 秒後放棄（預設 60，設 `0` 停用），並回傳一個明確指出是哪個 tool
  佔住 thread、已經跑多久的錯誤。已經開始執行的呼叫則永遠不會被 timeout——放棄等待並不會讓
  COM 呼叫停下來，只會把「還在跑」這個事實換成一個誤導人的錯誤。真的卡死時唯一的解法仍然是
  重啟 MCP server；`get_view_info` 則是一開始就不要去招惹它的方法。
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
