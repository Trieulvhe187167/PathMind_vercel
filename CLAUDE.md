# CLAUDE.md — PathMind (bản tùy biến)

PathMind là bản đổi tên + tùy biến của DeepTutor (HKUDS, Apache-2.0 — xem `LICENSE`,
`THIRD_PARTY_NOTICES.md`). File này tóm tắt **những gì đã làm thêm**, trạng thái từng chức năng
và các quy ước cần nhớ khi sửa code. Kiến trúc lõi (Tools / Capabilities / ChatOrchestrator / StreamBus)
nằm trong `pathmind/`.

> Cập nhật lần cuối: 2026-10-19 — Áp dụng D1–D10 (vai trò R&D/Educator, console riêng cho staff, nhánh cá nhân, điểm thưởng, chỉ còn GraphRAG + LightRAG, bỏ MCP/CLI/kênh IM). Trước đó (2026-09-30): Gói Plus + quyền theo gói (entitlements), Partner chat riêng tư với admin. Trước đó (2026-09-28): Skills cho mọi người dùng, Partners en/zh + chat phòng học, Cộng đồng (Skill Hub, Forum, báo cáo vi phạm, thông báo), bỏ Import from EduHub. Trước đó: VNPAY, dọn cài đặt hosted, web production, tín dụng lập chỉ mục.

---

## 1. Tổng quan trạng thái

| Mảng chức năng | Trạng thái | Ghi chú ngắn |
| --- | --- | --- |
| Đăng ký / đăng nhập nhiều người dùng | ✅ Xong | `/register` mở cho mọi người, người đầu tiên thành admin |
| Gói cước + hạn mức **tín dụng** theo giá model | ✅ Xong | Trừ theo từng lần gọi LLM (`services/llm/metrics.py`), chặn trước lượt trong `ChatOrchestrator` |
| Bảng giá API model + báo cáo chi phí/lãi gộp | ✅ Xong | `/admin/plans` (bảng giá), `/admin/payments` (chi phí theo model) |
| Thanh toán (checkout → xác nhận → kích hoạt) | ✅ Xong | VNPAY tự xác nhận + admin xác nhận tay / webhook HMAC / demo |
| Vòng đời subscription (hết hạn, gia hạn, huỷ gia hạn) | ✅ Xong | Tự hết hạn khi đọc; gia hạn cộng dồn |
| Trang Bảng giá `/pricing` (user) | ✅ Xong | Xem cả khi chưa đăng nhập |
| Admin: gói cước `/admin/plans` | ✅ Xong | CRUD gói, sửa giá/hạn mức/model/tính năng |
| Admin: người dùng `/admin/users` | ✅ Xong | Xem gói thật, cấp gói có thời hạn, huỷ gói |
| Admin: thanh toán `/admin/payments` | ✅ Xong | Xác nhận / từ chối / hoàn tiền, doanh thu |
| Hồ sơ `/profile` hiển thị gói & usage | ✅ Xong | |
| Hạn mức **dung lượng lưu trữ** | ✅ Xong | Kiểm tra cả lô khi upload KB, trả lại dung lượng khi xoá tệp/KB |
| Giới hạn tệp đính kèm chat theo gói | ✅ Xong | `get_chat_attachment_limits` lấy min(system.json, gói) |
| Cổng thanh toán VNPAY (QR, ATM, Visa/Master/JCB) | ✅ Xong (sandbox) | IPN + trang quay về; đã bỏ VietQR, Stripe, Crypto |
| Bạn bè (friends) | ✅ Xong | Tìm theo tên đăng nhập/email, mời ngược tự chấp nhận, chặn/bỏ chặn, huỷ lời mời |
| Chia sẻ Partner cho bạn bè | ✅ Xong | Chỉ chủ Partner, chỉ cho bạn bè; người nhận được **dùng** (chat), gỡ được |
| Phòng học chung (study rooms) | ✅ Xong (bản đơn giản) | Mã 6 ký tự, vào/rời/đóng/mời ra; **chat chung** (poll 2.5s) + hỏi Partner của phòng công khai (trừ credit người hỏi). Tạo phòng ngay ở tab "Friends & Study Rooms" |
| Chống spam đăng ký | ✅ Xong | Tối đa `REGISTER_MAX_PER_IP_PER_HOUR` (mặc định 5) tài khoản/IP/giờ |
| Cộng đồng: Skill Hub + Forum | ✅ Xong (bản đầu) | `/community` (forum kiểu Reddit) + `/community/skills` (kho skill, bấm Add là vào Skills của mình) |

Chú thích: ✅ hoàn thiện · 🟡 chạy được nhưng chưa đầy đủ · ⚠️ chưa làm / chỉ demo.

---

### RAG chạy thật, KB của R&D, cấu hình hệ thống trong console (2026-10-20)
- **Thư viện RAG đã cài**: `lightrag-hku==1.5.7`, `graphrag 3.3.0` (extras `rag-lightrag`, `graphrag` trong `pyproject.toml`). LightRAG đã thử index + hỏi thật thành công; GraphRAG mới kiểm tra mức sẵn sàng.
- **Key/model đang chạy (kiểm 2026-10-20)**: Gemini key cũ **hết hạn (401)**, OpenAI **hết credit (429)**, DeepSeek **hết số dư**, Anthropic còn chạy, proxy `api.xah.io` còn chạy. Model chat mặc định = `dungcsnd113/gemini-flash-3.8` (proxy, binding `custom`); embedding mặc định = `text-embedding-3-small` qua proxy (1536 chiều). Proxy model là **alias** của dòng giá `gemini-3.8-flash` ⇒ Plus/Pro/Unlimited dùng được; **gói Free chưa có model nào chạy được** (mọi model Free đều thuộc key hỏng).
- **R&D nối KB GraphRAG của mình**: `knowledge_tree.service.linkable_kbs()` liệt kê KB GraphRAG trong kho admin + kho của mọi tài khoản `rnd`; domain lưu `kb_name` = tên (kho admin, như cũ) hoặc `<user_id>/<tên>` (kho R&D); `resolve_kb_ref()` ra thư mục + tên, `unified._admin_graph_search` dùng nó. `GET /kb-options` trả thêm `options` (tên + chủ).
- **Cấu hình hệ thống chuyển sang console**: `/admin/config/<mục>` (`components/staff/pages/ConsoleSettingsPage.tsx`, dùng lại `SETTINGS_SECTIONS` + store/nháp/thanh Apply của Cài đặt): Providers & API keys, Language/Task/Embedding models, Web search, Voice, Multimodal, Capabilities, Knowledge & documents, Starting points, Runtime status. Cài đặt của app (`/settings`) giờ **chỉ còn mục cá nhân** cho mọi người (`SettingsAccessProvider mode="app"` ⇒ `hideAdminOnly` luôn true; console dùng `mode="console"`); admin mở link cũ `/settings/<mục hệ thống>` ⇒ chuyển sang `/admin/config/<mục>`. Trang General không còn khối "Set up chat first".
- **Nút quay lại console** trong giao diện học viên (`components/auth/UserMenu.tsx`): admin ⇒ "Back to Admin console" (`/admin`), R&D ⇒ "Back to R&D console" (`/rnd`).

### Seed, key LLM cũ, trang Models (2026-10-20)
- **Seed một file** (`services/seed.py`): khởi động ⇒ nạp mọi `data/seed/*.json` mới/đổi (bỏ `*.example.json`; nhớ hash ở `data/seed/.applied.json`). Gồm `accounts` (vai trò, gói; không có mật khẩu ⇒ sinh và ghi `data/seed/seed-accounts.txt`), `schema`, `knowledge` (domain viết dạng tài liệu có `@…`), `llm_providers` (key mã hoá khi ghi), `reward_rules`. Không ghi đè gì đã có. Mẫu: `seed/pathmind-seed.example.json` (không có mật khẩu/key); CLI `scripts/load_seed.py`. Test: `tests_pm/multi_user/test_seed.py`.
- **Key LLM từ bản cũ**: `scripts/import_llm_keys.py --catalog <model_catalog.json> [--rewrite-db]` gộp connection/profile theo id (chạy lại không trùng), `--rewrite-db` đọc `public.ai_providers` của bản `backend/` và giải mã bằng `data/secret.key`. Đã nhập 2026-10-20 từ `C:\PathMind_removed\db_backup\model_catalog_before_encrypt.json` (Gemini, Anthropic, OpenAI, DeepSeek) + proxy `api.xah.io` (binding `custom`). Khoá mã hoá key: `data/system/secrets.key` — **sao lưu riêng**, mất là phải nhập lại key.
- **Admin → Plans** chỉ còn gói cước (vẫn đọc bảng giá để tính % chi phí); **Admin → Models** (`/admin/models`) giữ bảng giá API model + lối tới Settings → Connections / LLM.

### Knowledge graph (2026-10-20) — thay cho "cây tri thức"
- Trên giao diện, công cụ AI và dữ liệu cho LLM mọi chỗ đều gọi là **knowledge graph**. Tên nội bộ giữ nguyên: module `services/knowledge_tree/`, API `/api/knowledge-tree`, bảng `knowledge_nodes`, `knowledge_tree_changes`.
- **Nút** (`KnowledgeNode`): `label` (kiểu/class theo schema), `layer` (tuỳ chọn, chữ tự do ≤12 ký tự, để trống được; không còn khái niệm principle/instance), `properties` (JSON key–value), `key` (ID đọc được, duy nhất trong domain, tự sinh từ tiêu đề, vd `linear-equations`), `names` (tên theo ngôn ngữ `{"vi":…, "en":…}`), `description` (mô tả ngắn), `provenance` (`source`, `confidence` 0–1, `note`), `created_by/at`, `updated_by/at`; `parent_id` của dàn bài = cạnh `PART_OF` ngầm.
- **Cạnh** `KnowledgeEdge` (nguồn → đích, `rel_type`, `properties`) trong cùng domain. **Schema** `KnowledgeLabel` + `KnowledgeRelationType`: bộ mặc định tự seed (`graph.ensure_schema`), R&D thêm/sửa/xoá (đổi tên kéo theo nút/cạnh; đang dùng ⇒ 409; `PART_OF` không xoá/đổi tên được). Thay đổi schema ghi lịch sử dưới `SCHEMA_SCOPE = "__schema__"`.
- **Chỉ R&D sửa** (`EDIT_ROLES = {"rnd"}`); admin chỉ xem graph, tài liệu, schema, lịch sử (`VIEW_ROLES`). Gộp/từ chối đề xuất: `require_rnd_only`. Chạy không đăng nhập (local-admin) được coi là R&D.
- **Tài liệu ⇄ graph** (`document_sync.py`, dùng cho Co-Writer "Update main graph", nhập tệp, mẫu): dưới mỗi tiêu đề có thể viết `@id`, `@name vi = …`, `@description`, `@source`, `@confidence`, `@label`, `@layer` (tuỳ chọn), `@prop k = v`, `@rel TYPE -> Tiêu đề|#id|tên {k: v}`, `@rel none`. Nút nhận ra theo `@id` trước rồi theo đường dẫn tiêu đề; mục không có loại directive nào thì giữ dữ liệu cũ; lỗi directive chỉ thành `warnings`. Xuất markdown ghi lại đủ directive (`@rel … -> #id`). Tiêu đề đánh số (TXT/PDF) bỏ phần số.
- **Mẫu** (`samples.py`, console → Samples): md/txt/docx/pdf cùng nội dung, đọc ra cùng 8 nút/6 quan hệ.
- **LLM**: công cụ `knowledge_graph` (actions `domains|schema|outline|node|neighbors|search`; tên cũ `knowledge_tree` vẫn nhận qua alias) và `knowledge_search` (thêm mục "Graph facts"). Quan hệ đưa cho LLM dạng `(Tên:Label {k: v}) -[REL {p}]-> (Tên:Label)` (`graph.triples`, `llm_view.py`).
- **Hai console tách bạch**: R&D ở `/rnd` (route group `(rnd)`: knowledge, samples, reviews, rewards chỉ xem, status), admin ở `/admin` (Business, Knowledge chỉ xem, System). `StaffGuard console="admin"|"rnd"` chỉ cho đúng vai trò, vai trò kia bị chuyển về console của mình; menu riêng ở `CONSOLE_NAV` (`components/staff/StaffSidebar.tsx`). Trang dùng chung nằm ở `components/staff/pages/*`, route chỉ re-export. Đăng nhập: admin → `/admin`, R&D → `/rnd`.
- Trang Main knowledge graph: tab Graph & documents (sửa nút: ID, tên, mô tả, nguồn, nhãn, layer tuỳ chọn, thuộc tính, quan hệ), **Diagram** (nút tròn màu theo nhãn, mũi tên màu theo loại quan hệ, chú giải bật/tắt từng loại; xếp hàng theo hướng quan hệ, không chia tầng), **Schema** (nhãn không có layer), **Edit history**. Cột `knowledge_labels.layer` cũ bị xoá khi khởi động (`maintenance.drop_retired_columns`). Test: `tests_pm/services/test_knowledge_graph.py`.

### Lịch sử sửa cây chính + PostgreSQL (2026-10-19)
- **Lịch sử + revert cây chính** (`services/knowledge_tree/history.py`, bảng `knowledge_tree_changes`): mọi thao tác của R&D/admin (domain tạo/sửa/xoá, nút thêm/sửa/di chuyển/xoá, nhập tệp, nhập cây, upload/xoá tài liệu, gộp đề xuất) chạy trong `tracked(...)`: so ảnh chụp domain trước–sau, lưu phần khác (`before`/`after` theo khoá `domain:|node:|source:<id>`). `POST /api/knowledge-tree/history/{id}/revert` đưa các thực thể về `before`; nếu có thay đổi mới hơn đụng cùng chỗ ⇒ 409 kèm số thay đổi cần revert trước. Revert cũng là một thay đổi (`reverts_id`) nên "Undo revert" được. Domain đã xoá vẫn khôi phục được (`deleted_domains`). Xoá tài liệu **không** xoá file gốc trong kho lưu trữ (để revert). Giao diện: console → Main knowledge tree → tab "Edit history". Test: `tests_pm/services/test_knowledge_tree_history.py`.
- **PostgreSQL**: `local.env.bat` đặt `DATABASE_URL=postgresql+psycopg://…/pathmind` + `DATABASE_SCHEMA=pathmind_app` (bảng của PathMind nằm trong schema riêng; schema `public` của DB đó là của bản viết lại `backend/`, đừng dùng chung). `connection.py` đặt `search_path` và tự `CREATE SCHEMA`. Tài khoản Postgres không có quyền tạo database. Chuyển dữ liệu SQLite: `python scripts/migrate_sqlite_to_postgres.py --schema pathmind_app` (bỏ qua bảng đích đã có dữ liệu, không xoá gì). Bản sao SQLite trước khi chuyển: `C:\PathMind_removed\db_backup\pathmind_before_pg_20261019.db`. Thiếu `psycopg` ⇒ backend không lên khi chạy `start.bat` (log `logs\backend.log`).

### Áp dụng quyết định D1–D10 (2026-10-19) — chi tiết từng module: `CHANGES_REBUILD.md`
- **Vai trò** `admin | rnd | educator | user`. Quyền chỉ theo vai trò + gói (D6): đã bỏ kho sách dùng chung của admin, quyền theo từng sách (`book_permission`) và grant KB của admin cho từng người (D1). Grant model/skill/partner/tool vẫn giữ.
- **Console staff** (route group `(admin)`): `app/(admin)/layout.tsx` = `StaffGuard` + `AppShell` với `components/staff/StaffSidebar.tsx` (nhóm Knowledge R&D / Business / System, lọc theo vai trò). R&D chỉ thấy cây chính, duyệt đề xuất, Rewards (chỉ xem), System Status; admin thấy hết. Không dùng chung sidebar với learner/educator; đăng nhập staff vào thẳng `/admin`. Đã bỏ `AdminTabs`.
- **Nhánh cá nhân** (`/api/knowledge-branch`, trang `/knowledge-branch` "My Knowledge"): mở cho **mọi tài khoản** (không còn khoá gói). Upload/tạo KB ⇒ nền chạy `overlay.auto_enrich_files` (dàn bài → nút, không tốn AI; bỏ qua tệp đã xử lý theo sha256, > 25 MB, định dạng không đọc được; lỗi không làm hỏng upload; không lưu bản sao thứ hai). Staff (admin/rnd) không bị làm giàu nhánh. `POST /enrich` thủ công: chỉ Educator/admin giữ file gốc và bị tính vào hạn mức dung lượng.
- **Đề xuất** (`/api/recommendations`): Educator gửi (cần quyền gói `contribute`), R&D/admin gộp nút vào cây chính hoặc từ chối; gộp ⇒ cộng điểm + email.
- **Mastery path trên nhánh**: chỉ đánh dấu thành thạo khi mọi tiên quyết đã thành thạo (409); không bỏ đánh dấu nút mà nút đã thành thạo khác phụ thuộc.
- **Điểm thưởng** (`services/rewards.py`): quy tắc `earn.node_mastered` (mỗi nút 1 lần) có trần `cap.daily_activity_points`/ngày UTC; `earn.recommendation_merged`, `earn.per_node_merged`. Chỉ **admin** sửa quy tắc / cấp điểm tay; R&D xem sổ. Tiêu điểm khoá `_SPEND_LOCK` (1 process). Đổi ngày gói bị từ chối nếu gói trả phí hiện tại không hết hạn (tránh thay gói vĩnh viễn bằng gói ngắn).
- **RAG (D2)**: KB mới chỉ GraphRAG/LightRAG; người dùng thường **chỉ LightRAG** (`_new_kb_provider` 403; `rag-providers` trả `selectable`, `CreateKbModal` lọc). GraphRAG dành cho cây chính của admin/rnd.
- `object_store.save/load(..., db=)`: truyền phiên đang mở khi đang trong giao dịch ghi (tránh phiên thứ hai rollback/khoá SQLite).
- Test: `tests_pm/` (chạy cùng `conftest` của `C:\PathMind_removed\tests\tests`), thêm `tests_pm/services/test_branch_rewards.py`.
- Còn tồn: dịch i18n chuỗi mới; `npm run contracts:generate`; mã hoá SMTP/S3 secret trong DB.

### Cây tri thức + công cụ truy hồi hợp nhất (2026-10-07)
- Menu **Knowledge Tree** (`/knowledge-tree`, API `/api/knowledge-tree`). **Chỉ admin** tạo/sửa/xoá (nhập PDF/DOCX/MD/TXT: tiêu đề → nút), mọi người đăng nhập chỉ đọc. Bảng `knowledge_domains` (có `kb_name` nối tới KB GraphRAG trong kho của admin), `knowledge_sources`, `knowledge_nodes`; code ở `services/knowledge_tree/` (`ingest.py`, `service.py`, `unified.py`).
- **Công cụ AI**: `knowledge_tree` (duyệt domains/outline/read/search) và `knowledge_search` (truy hồi hợp nhất: câu hỏi → `find_scope` chấm điểm từ khoá trên cây → hỏi KB GraphRAG đã nối với domain đó, kèm gợi ý chủ đề → gộp "Curated knowledge tree" + "GraphRAG" + nguồn/đường dẫn nút). Thiếu một nửa thì nửa còn lại vẫn trả lời, kèm `notes`. Chỉ gắn vào chat khi cây có nội dung (`has_knowledge_tree`).
- Nối KB với domain = admin **xuất bản** KB đó qua gia sư cho mọi người dùng (adapter dùng `admin_kb_base_dir`, không cần grant từng KB). `POST /ask` (admin) để thử truy hồi. Test: `tests_pm/services/test_knowledge_tree_main.py`.

### Quyền riêng tư chat Partner (2026-09-29)
- **Admin không còn đọc được chat của người khác với Partner.** Trước đây admin luôn dùng "kho chung" của mỗi Partner (`data/partners/<id>/sessions/`), nơi cũng chứa tin nhắn kênh IM chưa liên kết tài khoản, và mọi admin đều đọc được kho chung (lịch sử + luồng hoạt động trực tiếp) của mọi Partner.
- Giờ `personal_actor_id(actor, partner_id)` (`services/partners/interaction.py`): trên Partner **người khác sở hữu**, admin có thư mục chat riêng `users/<admin_id>/sessions` như người dùng thường; chỉ trên Partner do admin quản lý (không có `owner_id`) hoặc của chính admin thì admin mới dùng kho chung như cũ.
- Kho chung chỉ **chủ Partner** đọc được (`multi_user/partner_access.can_see_shared_partner_threads`; Partner không có chủ ⇒ admin). Áp dụng cho `GET /api/partners/{id}/history` (`include_shared`) và luồng hoạt động WebSocket.
- Lượt trả lời đang chạy (`live_turns`) gắn theo từng người (`PartnerManager._live_turn_key`): gõ trùng `session_key` không nhìn/dừng được câu trả lời của người khác.
- Tin nhắn cũ admin đã gửi vào kho chung của Partner người khác vẫn nằm ở đó (chủ Partner vẫn thấy); admin không còn xem được. Test: `tests/services/test_partner_privacy.py`.

### Cộng đồng: Skill Hub + Forum (2026-09-28)
- Menu **Community** (sidebar). Backend `api/routers/community.py` tại `/api/community`, logic đóng gói/cài skill ở `services/community.py`. Bảng mới: `community_skills`, `community_skill_installs`, `forum_posts`, `forum_comments`, `community_votes` (tự tạo khi khởi động).
- **Skill Hub** (`/community/skills`, chi tiết `/community/skills/[slug]`): tìm kiếm, lọc theo nhóm (academics, languages, skills-interests, companions, educators, productivity, other), sắp xếp theo lượt thêm/thích/mới. **Add** ⇒ chép gói vào Skills của chính người dùng qua `SkillService.install_tree` (bỏ `always:`, chỉ nhận file text trong whitelist, ghi nguồn `hub = "community"` vào `.hub-lock.json`, gắn tag `community`); trùng tên ⇒ 409, giao diện hỏi "Replace". `install_count` đếm theo người.
- **Chia sẻ skill** từ: skill của mình (`source=skill`, kèm file phụ), tài liệu Co-Writer (`source=cowriter`), dán markdown (`source=text`), hoặc upload `SKILL.md`/`.md`/`.zip` (`POST /skills/upload`, ≤5 MB, file nhị phân bị bỏ). Nút chia sẻ có ở Hub, ở từng skill trong Learning Space → Skills, và trên thanh công cụ Co-Writer ("Share as a community skill", lấy nội dung đang soạn). Tác giả sửa/thay gói (tăng `version`) hoặc gỡ; admin gỡ = ẩn (`is_hidden`). Người đã thêm vẫn giữ bản sao.
- **Forum** (`/community`, bài `/community/posts/[id]`): chủ đề general, help, skills, study, feedback, announcements (chỉ admin đăng); vote ±1, bình luận lồng nhau, sắp xếp hot/new/top, tìm kiếm, gắn skill vào bài ("Discuss in the forum"). Tác giả sửa/xoá (xoá mềm, luồng trả lời vẫn đọc được); admin ghim, khoá, xoá. Markdown hiển thị với `allowHtml={false}`.
- Giới hạn: 10 bài/giờ, 60 bình luận/giờ, 20 skill/ngày mỗi người (429). Thông báo lỗi `services.i18n` khoá `community.*` (en/zh). Test: `tests/services/test_community.py`.
- **Báo cáo vi phạm**: nút "Report" trên bài, bình luận, skill của người khác (lý do: spam, harassment, inappropriate, misinformation, copyright, other + ghi chú). Mỗi người báo 1 lần/nội dung (báo lại = cập nhật lý do), tối đa 30 báo cáo/giờ, không báo nội dung của mình. Bảng `community_reports`. Admin xem ở tab **Moderation** (`/community/moderation`, `GET /reports?status=open|resolved`, gộp theo nội dung, nhiều báo cáo lên trước) ⇒ **Keep (dismiss)** hoặc **Remove** (`POST /reports/resolve`: xoá mềm bài/bình luận, ẩn skill, đóng mọi báo cáo của nội dung đó).
- **Thông báo** (bảng `community_notifications`): có người bình luận bài của tôi (`post_comment`), trả lời bình luận của tôi (`comment_reply`), thêm skill của tôi (`skill_installed`, lần đầu mỗi người), admin gỡ nội dung của tôi (`content_removed`). Không tự thông báo cho chính mình. Chuông ở đầu trang Community (danh sách, đánh dấu đã đọc, bấm để mở bài/skill); số chưa đọc hiện cạnh mục Community ở sidebar (`lib/community-unread.ts` + `hooks/useCommunityUnread.ts`, hỏi `GET /notifications/unread-count` mỗi 60 giây khi tab đang mở).
- Đã bỏ nút **Import from EduHub** trong Learning Space → Skills (thay bằng "Community skills"); `EduHubImportModal.tsx` chuyển sang `C:\PathMind_removed\pm55`. API `/api/skills/hub/*` vẫn còn (CLI dùng).

### Thanh toán chỉ qua VNPAY (2026-09-28) — thay VietQR, Stripe, Crypto
- **Cổng duy nhất: VNPAY** (`services/vnpay.py`, API v2.1.0): trên trang VNPAY người dùng chọn **VNPAY-QR** (quét bằng app của hầu hết ngân hàng), **thẻ ATM nội địa** hoặc **Visa/Mastercard/JCB**. `Gateway = Literal["vnpay"]`; gửi `bank_transfer`/`stripe`/`crypto` ⇒ 422. Code VietQR/Stripe đã gỡ (chuyển sang `C:\PathMind_removed`); payment cũ vẫn hiển thị bình thường.
- Checkout ⇒ payment pending (giá USD × `BILLING_USD_VND_RATE` = `amount_vnd`) + URL thanh toán đã ký (`vnp_TxnRef` = mã DT…, `vnp_Amount` = VND×100, giờ GMT+7, hết hạn 30 phút, `vnp_SecureHash` = HMAC-SHA512 trên chuỗi tham số sắp xếp theo tên, giá trị `quote_plus`).
- Xác nhận (đều kiểm tra chữ ký + đúng số tiền, gọi lại nhiều lần không cộng thêm):
  - **IPN** `GET /api/billing/vnpay/ipn` (VNPAY gọi server; phải khai báo với VNPAY, cần HTTPS công khai). Trả `{"RspCode","Message"}`: `00` thành công, `02` đã xác nhận, `01` không có đơn, `04` sai số tiền, `97` sai chữ ký, `99` lỗi.
  - **Trang quay về** `/pricing?vnp_…` ⇒ frontend gửi các tham số `vnp_*` tới `POST /api/billing/payments/vnpay-return` (chỉ chủ đơn) — chạy được cả khi VNPAY chưa gọi được IPN (thử trên máy).
  - Thành công khi `vnp_ResponseCode == "00"` và `vnp_TransactionStatus == "00"`; khác ⇒ payment `failed` (VD `24` = khách huỷ). `payments.gateway_reference = vnpay:<vnp_TransactionNo>`.
- Cấu hình trong `local.env.bat` (git-ignore; mẫu `local.env.example.bat`): `VNPAY_TMN_CODE`, `VNPAY_HASH_SECRET`, `VNPAY_ENV=sandbox|production`, `PATHMIND_PUBLIC_URL`. Sandbox đăng ký tại https://sandbox.vnpayment.vn/devreg/. Admin → Payments có thẻ VNPAY (sandbox/production, mã TMN, IPN URL cần khai báo, Return URL); API `GET /api/admin/vnpay/status`. Bản thật: `BILLING_DEMO_MODE=false`, `VNPAY_ENV=production`. Test: `tests/services/test_vnpay.py`.

### Dọn cài đặt cho bản hosted (2026-09-27)
- (Tích hợp VietQR làm cùng đợt này đã được thay bằng VNPAY ngày 2026-09-28.)
- **Đã bỏ khỏi Cài đặt** (không dùng khi chạy như một trang web): Network (cổng/CORS — đặt bằng biến môi trường), Workspace (thư mục gốc/di chuyển dữ liệu trên máy chủ), Data migration, Video Learning (Invidious tự host). Link cũ tới các trang này tự về General; file giao diện chuyển sang `C:\PathMind_removed`. Breadcrumb workspace trong chat không còn lộ đường dẫn thư mục máy chủ.
- **Chặn quyền ghi cấu hình chung**: `PUT /api/capabilities/settings`, `PUT /api/memory/settings` giờ chỉ admin (trước đây user thường gọi thẳng API là sửa được cho cả hệ thống); trang Memory chỉ admin thấy. Các endpoint workspace đụng tới file hệ thống (`/data/*`, `migrate-root`, `{id}/migrate`, `system-snapshot`, `PUT ""`, `validate`) chỉ admin; danh sách/tạo/đổi tên workspace của chính user vẫn mở. 

### Web chạy bản production + trạng thái đăng nhập tức thì (2026-09-26)
- **`start.bat` giờ chạy frontend bản production** (`scripts/start_web_prod.py`): build Next.js một lần vào `web/.next-pathmind` rồi chạy `standalone/server.js` (nghe `0.0.0.0:3782`, dùng được từ điện thoại cùng mạng). Dùng lại logic của launcher (`_ensure_source_production_build`): **tự build lại khi có file trong `web/` thay đổi** — lần khởi động đầu sau mỗi lần cập nhật mất vài phút (xem `logs\frontend.log`), các lần sau vào ngay. `start.bat dev` = chạy `next dev` như cũ (hot reload, chậm) khi cần sửa giao diện. `next dev` chính là nguyên nhân khựng: mỗi trang compile lần đầu 10+ giây, React bản dev, không minify.
- Nếu `next dev` báo 404 hàng loạt sau khi khởi động lại: chuyển `web\.next` sang `C:\PathMind_removed\_cache` rồi chạy lại (bộ đệm Turbopack hỏng).
- **Trạng thái đăng nhập dùng chung** (`lib/auth.ts`: `peekAuthStatus`, `subscribeAuthStatus`, `refreshAuthStatus`, `clearAuthSnapshot`): trạng thái gần nhất được lưu `localStorage["pathmind-auth-snapshot"]` (chỉ id, tên, role, avatar — không email/policy) và đồng bộ giữa các tab bằng `BroadcastChannel("pathmind-auth")`. `useAuthStatus`/`useAuthSnapshot` (`useSyncExternalStore`) hiện ngay tài khoản đúng rồi kiểm tra lại nền (cache 15 giây). `login()` đợi đọc xong tài khoản mới trước khi chuyển trang; `logout()`/xoá tài khoản ⇒ `clearAuthSnapshot()`. `UserMenu`, `SettingsAccessProvider`, `AdminOnlyGuard` dùng store này nên không còn nhấp nháy "chưa đăng nhập"/thiếu mục admin. Quyền thật vẫn do backend kiểm tra.

### Embedding + lập chỉ mục: model do admin chọn, tính tín dụng (2026-09-25)
- **Embedding model**: user thường không chọn được. `_resolve_embedding_form` chỉ nhận model embedding mặc định (Cài đặt → Models → embedding đang active) hoặc model KB **đang** gắn (reindex); gửi model khác ⇒ 403. `GET …/rag-pipelines/model-options` với user thường chỉ trả model mặc định kèm `locked: true` → giao diện (`EmbeddingModelSelector`) hiện dạng chỉ đọc "Chosen by the administrator…". KB cũ gắn model khác khi reindex sẽ chuyển sang model mặc định. **Chưa cấu hình embedding active ⇒ user thường không tạo được KB LlamaIndex/LightRAG/GraphRAG.**
- **LLM lập chỉ mục (LightRAG)**: ô chọn vốn đã lọc theo gói (`/api/settings/llm-options`). Không chọn ⇒ user thường dùng **model mặc định của gói** (`_freeze_default_indexing_llm` → `allowed_llm_options().active`, VD Free = deepseek-flash) thay vì model LightRAG của admin; frontend cũng rơi về model mặc định của user khi model LightRAG của admin không nằm trong gói.
- **Tính tín dụng** (`services/indexing_credits.py`): mỗi lô embedding (`EmbeddingClient.embed`/`embed_contents`) bị trừ vào user đang chạy theo bảng giá `model_prices` (token do provider báo, không có thì ước tính ký tự/3.5). Provider local (`is_local`: Ollama, vLLM…) miễn phí. Model embedding không có trong bảng giá dùng dòng `*embedding` ($0.15/1M), **không** dùng dòng `*` của LLM ($2/1M). Đã seed giá `text-embedding-3-small/large`, `ada-002`, `gemini-embedding-001`. LLM trích xuất thực thể của LightRAG vốn đã bị trừ qua `CallMeasurement` (chạy dưới `user_context(owner)`).
- **Dừng khi hết tín dụng**: `RAGService.initialize/add_documents` chạy trong `indexing_scope()`; trong đó mỗi lô embedding và mỗi lệnh gọi LLM của LightRAG gọi `ensure_indexing_credits()` (kiểm tra hạn mức ngày/tháng, cache 5 giây) ⇒ hết tín dụng thì job báo lỗi `IndexingCreditsExhausted` (KB giữ bản index cũ). Trước khi bắt đầu: tạo KB có file, upload, reindex, sync GitHub/web ⇒ `_indexing_credit_precheck()` trả 403 nếu đã hết. Truy vấn lúc chat (embedding câu hỏi) vẫn bị tính nhưng không bị chặn ở đây (lượt chat đã được `check_turn_allowed` chặn). Admin không bao giờ bị giới hạn. Test: `tests/services/test_indexing_credits.py`.

### Knowledge Center: phân quyền admin / người dùng (2026-09-25)
- Người dùng thường **vẫn dùng Knowledge Center**: tạo KB, upload (tính vào hạn mức dung lượng của gói), thư mục/xoá file, KB mặc định, reindex, nguồn GitHub/web/EduHub, chọn KB trong chat.
- **Chỉ admin** (`_ADMIN_ONLY = [Depends(require_admin)]` trong `api/routers/knowledge.py`): mọi `PUT /knowledge-bases/rag-pipelines/*/config`, `rag-pipelines/active-model` (trước đây user thường ghi được vào catalog model của admin!), `rag-providers/{p}/mode`; các chức năng đọc thư mục **trên máy chủ** (`connect-folder`, `probe-folder`, `connect-obsidian`, `connect-marginnote4`, `{kb}/link-folder`, `{kb}/sync-folder/*`) và nối máy chủ/tài khoản ngoài (`probe|connect-lightrag-server`, `list|probe|connect-ima`, WeKnora). Muốn giới hạn thư mục admin được nối: biến môi trường `PATHMIND_LINKED_FOLDER_ROOTS`.
- **Nội dung kéo từ GitHub/web tính vào hạn mức dung lượng** của gói: `sync-github` / `sync-web` đo kích thước `raw/` của KB trước–sau mỗi nguồn và ghi chênh lệch bằng `update_user_storage` (trang bị xoá khi đồng bộ ⇒ trả lại dung lượng). Hết dung lượng ⇒ từ chối đồng bộ (403, `billing.storage_full`). Kích thước một lần crawl không biết trước nên lần đồng bộ cuối có thể vượt nhẹ hạn mức; khi đó các lần upload/đồng bộ sau bị chặn đến khi xoá bớt. Xoá KB/tệp trả lại dung lượng như upload. Đồng bộ nền định kỳ của GitHub chỉ chạy cho kho của admin (không giới hạn).
- Nguồn web: chặn địa chỉ nội bộ ngay khi thêm (crawler vốn đã chặn từng lần chuyển hướng); user thường bị giới hạn `KB_WEB_SOURCE_MAX_PAGES` (mặc định 50) và `KB_WEB_SOURCE_MAX_DEPTH` (3). Nguồn GitHub phải đúng dạng `owner/repo`.
- Giao diện (`KnowledgePage` → `useKnowledgeAdminAccess`): user thường không thấy tab "Knowledge engines", modal tạo KB không có "Link existing" và không có engine LightRAG Server / WeKnora / IMA. Test: `tests/services/test_knowledge_admin_only.py`.

### Đổi thư mục C:\DeepTutor → C:\PathMind + ẩn Personalization (2026-09-25)
- Kho workspace (`data/**/.runtime/workspaces.sqlite3`, metadata `root`, binding cũ trong settings) lưu **đường dẫn tuyệt đối**. Sau khi đổi tên/chuyển thư mục cài đặt, chat báo "The selected workspace folder does not exist". `services/workspace/relocate.py` (`relocate_stale_path`) tự neo lại đường dẫn cũ vào `data/` hiện tại khi đường dẫn cũ không còn và vị trí tương ứng có thật; `catalog._catalog()` ghi lại hàng đã sửa. Áp dụng cả khi chuyển `data/` sang server khác.
- Learning Space: nhóm **Personalization** (Personas, Skills, MCP, CLI Apps, Memory) chỉ hiện cho **admin** khi bật đăng nhập (`components/common/AdminOnlyGuard.tsx`: `usePersonalizationAccess`, guard trong `SpaceMain` cho `/space/personas|skills|mcp|cli-apps` và `app/(utility)/memory/layout.tsx`). User thường vào URL trực tiếp ⇒ về `/space`. Chỉ ẩn giao diện; API backend không đổi, trí nhớ (memory) vẫn chạy ngầm.

### Model mặc định theo gói (2026-09-25)
- Cột mới `subscription_plans.default_model` (sửa ở `/admin/plans` → "Default model", phải nằm trong `allowed_models`). Người dùng **chưa tự chọn model** sẽ dùng model này; trống ⇒ model mặc định của deployment (Cài đặt → Models). Admin không bị ảnh hưởng.
- Gói **Free mặc định `deepseek-v4-flash`** (×1.9 tín dụng, catalog đang đặt tên `deepseek-flash` — khớp qua alias bảng giá) thay vì `gemini-3.6-flash` (×5.4): cùng hạn mức dùng được ~3 lần nhiều hơn (tạo đề Quiz ~38k tín dụng thay vì ~108k). Seed chỉ điền khi cột còn NULL; admin xoá trống thì lưu "" và không bị ghi đè.
- Áp dụng ở: `model_access.allowed_llm_options` (đánh dấu `active` cho ô chọn model), `request_preparer` (lượt không có `llm_selection`), và `orchestrator` giờ quy `llm_selection {profile_id, model_id}` ra tên model thật để kiểm tra gói/ước tính tín dụng đúng model đang dùng (trước đây luôn tính theo model mặc định của deployment).

### Tự xoá tài khoản (GDPR) + admin chỉ khoá/mở khoá (2026-09-25)
- **Chỉ chủ tài khoản được xoá**: Cài đặt → Profile → Delete account (nhập mật khẩu + gõ `DELETE`) ⇒ `POST /api/auth/account/delete` (sai mật khẩu tính vào khoá đăng nhập). Admin cuối cùng không tự xoá được (409). Xoá xong ⇒ về `/login?deleted=1`.
- **Admin không xoá được tài khoản** (đã bỏ `DELETE /api/auth/users/{username}`). Admin chỉ **khoá / mở khoá**: `PUT /api/auth/users/{username}/status {disabled}` (nút ổ khoá ở `/admin/users`, nhãn "Locked"). Khoá ⇒ giữ nguyên dữ liệu, đăng xuất mọi phiên ngay, thu hồi thiết bị, không đăng nhập được (403 `account_disabled` — chỉ báo khi mật khẩu đúng). Không tự khoá mình, không khoá admin đang hoạt động cuối cùng.
- Xoá thực hiện ở `services/account_deletion.py`: **xoá hẳn** bản ghi đăng nhập (tên, email, mật khẩu, avatar), toàn bộ `data/users/<id>/` (chat, ghi chú, sách, KB, partner, memory, file, key cá nhân), grant/thiết bị/secret MCP–CLI, bạn bè, chia sẻ partner, thành viên phòng (phòng do user làm chủ bị đóng), mã xác thực email. **Giữ lại** thanh toán + usage/chi phí (nghĩa vụ kế toán) nhưng dòng `users` bị ẩn danh (`deleted~<id8>~<ts>`, không email/tên, inactive). Gói trả phí đang chạy kết thúc ngay, không tự hoàn tiền.
- **Phiên cũ hết hiệu lực ngay**: `decode_token` kiểm tra tài khoản còn tồn tại, đúng id và không bị khoá (đọc lại `users.json` chỉ khi file đổi).

### Bảo mật API key & đăng nhập (2026-09-25)
- **Mã hoá API key khi lưu** (`services/secret_box.py`): mọi trường bí mật (`api_key`, `*token*`, `*secret*`, `extra_headers`…) trong `model_catalog.json` (cả catalog cá nhân) và `settings_draft.json` được lưu dạng `enc:v1:<Fernet>`. `ModelCatalogService` tự giải mã khi đọc/mã hoá khi ghi; file cũ dạng chữ thường được tự chuyển khi đọc lần đầu. Khoá: biến môi trường `PATHMIND_SECRETS_KEY` (khuyên dùng khi deploy), không có thì tự tạo `data/system/secrets.key`. **Mất/đổi khoá ⇒ không đọc được key** (vẫn giữ nguyên bản mã hoá trên đĩa, trả khoá cũ là dùng lại được) — nhớ sao lưu khoá riêng.
- **Đổi endpoint phải nhập lại key**: khi lưu cấu hình mà `base_url` đổi **host** (hoặc đổi `proxy`) và ô key vẫn là `***`, key cũ **không** được mang sang (bị xoá, ghi log cảnh báo) — tránh admin/kẻ chiếm admin trỏ connection sang server lạ để hứng key thật. Đổi path trên cùng host thì vẫn giữ key.
- **Khoá đăng nhập khi sai nhiều**: `POST /api/auth/login` — sai `LOGIN_MAX_FAILURES` (mặc định 5) lần trong `LOGIN_LOCK_MINUTES` (15) phút cho cùng IP+tài khoản ⇒ 429 `detail.code = "login_locked"`; một tài khoản bị sai 4× số đó từ nhiều IP cũng bị khoá. Chỉ tin `X-Forwarded-For` khi `PATHMIND_TRUST_PROXY=1`.

### Theme theo tài khoản (2026-09-25)
- Theme được lưu **theo từng tài khoản** (file `settings/interface.json` của chính user, đổi ở Cài đặt → Appearance → Apply, lưu qua `PUT /api/settings/ui`). localStorage `pathmind-theme` chỉ là **bản đệm** để `ThemeScript` tô đúng màu trước khi React tải.
- `GET /api/settings/ui` (public) giờ dùng `optional_auth` (`api/routers/auth.py`): có cookie đăng nhập ⇒ trả theme/ngôn ngữ **của user đó**; chưa đăng nhập ⇒ như cũ. Trước đây endpoint này luôn đọc cấu hình mặc định nên theme hiển thị lệch với tài khoản.
- **Theme mặc định** khi chưa đăng nhập và cho tài khoản mới chưa chọn theme: biến môi trường `PATHMIND_DEFAULT_THEME` (snow = "Default" | light | dark | glass; mặc định snow). Khách chưa đăng nhập luôn nhận theme này (không bao giờ thấy theme cá nhân của admin). Đăng xuất ⇒ xoá theme đệm trong trình duyệt và về theme mặc định. Không còn tự theo chế độ sáng/tối của hệ điều hành.
- `components/common/AccountThemeSync.tsx` (gắn trong `app/layout.tsx`): mỗi lần mở app, nếu đã đăng nhập thì lấy theme của tài khoản và áp dụng nếu khác bản đệm (đổi tài khoản trên cùng máy, hoặc đổi theme ở máy khác). `SettingsStore` khi tải cũng áp dụng theme của tài khoản để ô chọn theme luôn khớp với giao diện.

### Đăng ký chi tiết + xác thực email + sửa DB (2026-09-25)
- **Đăng ký** (`POST /api/auth/register`, model `SignupRequest`): họ tên (2–120), email (duy nhất, không phân biệt hoa thường), username tuỳ chọn (trống ⇒ dùng email), mật khẩu ≥ 8 ký tự có cả chữ và số (không trùng username/email), nhập lại mật khẩu, bắt buộc tick **Điều khoản** (`/terms`). Lỗi trả `detail = {code, field, message}` để form tô đỏ đúng ô. Đăng nhập được bằng username **hoặc** email.
- Thông tin hồ sơ (`full_name`, `email`, `email_verified`, `email_verified_at`) nằm trong kho định danh (`identity._profile_fields`, `update_profile`, `find_user_by_email`) và được sao sang bảng `users` (cột mới `full_name`, `email_verified`, `email_verified_at`).
- **Xác thực email** (`services/email_verification.py`, bảng `email_verifications`): mã 6 số, chỉ lưu HMAC, hết hạn 30 phút, tối đa 5 lần nhập sai, gửi lại cách nhau 60 giây. Endpoint công khai `POST /api/auth/email/verify {email, code}` và `POST /api/auth/email/resend {email}` (luôn trả ok, không lộ email có tồn tại hay không). Admin: `POST /api/auth/users/{username}/verify-email`; email do admin nhập khi tạo user được coi là đã xác thực.
  - **Chưa cấu hình SMTP** (`SMTP_HOST` trống): mã chỉ ghi vào log backend, tài khoản **không bị chặn** (chỉ ghi "chưa xác thực").
  - **Có SMTP**: user thường có email chưa xác thực bị chặn đăng nhập (403, `detail.code = "email_not_verified"`) → giao diện chuyển sang bước nhập mã. `EMAIL_VERIFICATION_REQUIRED=false` để không bao giờ chặn. Tài khoản cũ không có email không bị ảnh hưởng.
- Profile (Cài đặt → Profile) có thẻ **Thông tin tài khoản**: sửa họ tên (`PUT /api/auth/profile/details`), xem email + trạng thái, gửi/nhập mã xác thực. Trang admin Users hiện họ tên/email/trạng thái và nút "Mark verified"; ô tìm kiếm tìm cả họ tên/email.
- **Sửa DB khi khởi động** (`database/maintenance.py`, gọi từ `init_database()`): gộp dòng trùng rồi tạo unique index `uq_friendship_pair`, `uq_room_member`, `uq_partner_share_target`, `uq_usage_log_day` (usage trùng ngày được **cộng dồn**); đồng bộ bảng `users` với kho định danh (đúng id + username thật, email, họ tên, trạng thái khoá). Dòng "mồ côi" (không có tài khoản) **không bị xoá**: đổi tên thành `…~orphan~<id8>` và `is_active=False`. Chạy lại nhiều lần vẫn an toàn.
- **Hộp xác nhận**: không dùng `window.confirm()` (trình duyệt nhúng trả `false` ngay ⇒ nút "Delete chat" không làm gì). Dùng `await confirmAction(t("…?"))` từ `web/lib/confirm.ts`; `ConfirmHost` (gắn trong `app/layout.tsx`) hiển thị `ConfirmDialog`. `window.alert` ⇒ `notify` (toast).

### Tài khoản trong Cài đặt + đa ngôn ngữ cho gói cước (2026-09-24)
- Sidebar: chân sidebar chỉ còn **thẻ người dùng** (`components/auth/UserMenu.tsx`), bấm vào mở thẳng **Cài đặt → Profile**. **Đăng xuất** và **Admin** (chỉ admin) nằm ở cuối thanh điều hướng Cài đặt (`components/settings/SettingsNav.tsx`) và nút Sign out trên trang Profile.
- Sidebar không còn nhóm **More**: mọi tính năng (kể cả Co-Writer) luôn hiện; vẫn kéo-thả đổi thứ tự được. Đã bỏ chấm trạng thái phiên bản (`VersionBadge`).
- Đã bỏ trang **Cài đặt → About** (`/settings/about` chuyển về General). Ghi nguồn DeepTutor/Apache-2.0 nằm ở `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`.
- Hồ sơ chuyển vào **Cài đặt → Profile** (`/settings/profile`, `ProfileSettingsSection`); `/profile` tự chuyển hướng.
- **Cài đặt → Plans & billing** (`/settings/billing`, `BillingSettingsSection`): gói hiện tại, hạn mức, huỷ/bật lại gia hạn,
  lịch sử thanh toán; nút sang `/pricing` để mua/nâng cấp. Dùng chung `components/billing/SubscriptionOverview` + `PaymentHistory`.
  Hai trang này chỉ hiện khi bật đăng nhập (`authOnly` trong `settings-pages.ts`).
- Trang giá, thanh toán, hồ sơ và các trang admin gói/giao dịch/bảng giá dùng i18n (`t("English key")`, bản dịch trong
  `web/locales/{en,zh}/app.json`) — theo ngôn ngữ ở Cài đặt → General. Khoá "Plan" của gói cước dùng context: `t("Plan", { context: "billing" })`.
- Thông báo backend về gói/hạn mức/thanh toán dùng `pathmind.services.i18n.t("billing.*" | "admin.*")` theo ngôn ngữ giao diện của từng người dùng.
  Ghi chú lưu trong DB (`payments.note`) viết bằng tiếng Anh.

### Đổi tên sang PathMind (2026-09-24)
- Package Python `deeptutor` → `pathmind`; biến môi trường `DEEPTUTOR_*` → `PATHMIND_*`;
  mọi chữ "DeepTutor" trên giao diện/email/i18n → "PathMind"; logo/favicon/banner mới trong `web/public`.
- DB đổi tên `data/user/deeptutor.db` → `pathmind.db`. Cookie/localStorage đổi tiền tố ⇒ người dùng đăng nhập lại một lần.
- Giữ nguyên link tới dịch vụ/dự án gốc: EduHub (`eduhub.deeptutor.info`), trang About ghi nguồn DeepTutor.
- Kiểm tra cập nhật phiên bản (release của DeepTutor) **tắt mặc định** (`version_check_enabled=false`).
- Tiện ích đọc (read aloud, quiz, …) có danh sách built-in dự phòng khi chạy từ source không cài package.

### Dọn file không cần để chạy (2026-09-24)
Đã chuyển sang `C:\PathMind_removed\` (kèm `DeepTutor_source_backup.zip` là bản sao mã nguồn trước khi đổi):
tài liệu & README dịch, `assets/`, `docs-for-user/`, CONTRIBUTING/CODE_OF_CONDUCT/CITATION/AGENTS/SKILL;
Docker/compose, `.github/`, `packaging/`, `deeptutor_web/`; CLI `deeptutor_cli/`, pre-commit, import-linter,
script dev (`scripts/check_*`, `update.py`, …); toàn bộ test (`tests/`, `web/tests`, cấu hình Playwright/Vitest).

### Đã loại bỏ (2026-09-24)
- Trang Cài đặt **Attachments** (+ `PUT /api/settings/chat-attachments`): giới hạn tệp đính kèm giờ theo gói cước.
- Trang Cài đặt **Plans & Billing**: trùng `/pricing` và thẻ gói ở `/profile`.
- Trang Cài đặt **Usage statistics** (+ `GET /api/settings/usage`): thay bằng tín dụng/chi phí ở `/pricing` và `/admin/payments`.
- **Guardian & Learner profile**: trang Cài đặt, editor trong `/admin/users`, API `/api/multi-user/guardians…`,
  `/api/multi-user/learners/…`, `/api/auth/…/learner-profile`, `lib/guardian-api.ts`.
  Module `multi_user/guardians.py`, `learner_profile.py` và preset tài khoản `learner` (giới hạn tính năng học)
  vẫn giữ vì phần lõi còn dùng. `learner_profile` của **Mastery path** (hồ sơ học theo chủ đề) là tính năng khác, vẫn giữ.
- `web/contracts/generated/api.ts` + `openapi.json` còn mô tả các endpoint cũ → chạy `npm run contracts:generate` khi tiện.

## 2. Kiến trúc phần Subscription

### Hai kho dữ liệu người dùng (quan trọng!)
- **Kho định danh (nguồn sự thật):** `data/user/auth_users.json` qua
  `pathmind/services/auth.py` / `multi_user/identity.py`. Đăng nhập, mật khẩu,
  role (nằm trong JWT) đều ở đây. API: `/api/auth/users…`.
- **Database quan hệ (SQLAlchemy, mặc định SQLite `data/user/pathmind.db`,
  hoặc `DATABASE_URL`):** chỉ là *bản sao* để làm khoá ngoại cho subscription,
  payment, usage, friends… Dòng `users` được tạo **lười** bởi
  `quota_service.ensure_db_user()`.
- Quyết định phân quyền luôn dùng **role trong JWT** (truyền `role=` vào các hàm
  quota), không dùng `users.role` trong DB.
- User không có dòng trong DB ⇒ được tính là **gói Free** (không bao giờ được
  "không giới hạn").

### Tín dụng (credits) — cách tính hạn mức
- `services/model_pricing.py`: bảng giá `model_prices` (USD/1M token: vào, cache, ra) →
  chi phí thật của mỗi lần gọi → **tín dụng = chi phí / (BILLING_CREDIT_USD_PER_MTOK, mặc định $0.25) × 1M**.
  1 tín dụng ≈ 1 token của model rẻ nhất; Sonnet 4.6 ≈ ×22, Gemini 3.6 Flash ≈ ×5.4.
- Tra giá: bỏ tiền tố `models/…`, khớp id/alias, rồi id dài nhất là tiền tố (`…-preview`),
  không có thì dùng dòng `*` (giá an toàn, đắt). Provider `openai_codex` (tài khoản riêng của user) = 0.
- Trừ tín dụng: hook `_charge_current_user` trong `CallMeasurement.finish` → `quota_service.record_llm_call`
  (ghi `usage_logs.credits_used/cost_usd` và `usage_model_logs` theo model). Mọi đường gọi LLM có user context đều bị tính.
- Chặn trước lượt: `check_turn_allowed` = model có trong gói + còn đủ tín dụng cho **ước tính theo capability**
  (`CAPABILITY_TOKEN_ESTIMATE` × hệ số model), tránh một lượt deep_research vượt hạn mức lớn.
- Cột `max_tokens_per_day/month` của plan giờ mang nghĩa **tín dụng** (giữ tên cột để khỏi migrate).
- Hạn mức mặc định giữ chi phí API tối đa ≈ 55% giá bán: Free 1,5M/tháng (≈$0.38), Pro 22M (≈$5.50),
  Enterprise 66M (≈$16.50). Trang admin hiện badge "Chi phí API tối đa … % giá bán" cho từng gói.
- `init_db.seed_initial_data` tự chuyển các gói còn giữ nguyên số liệu bản đầu sang hạn mức/model mới
  (không đụng gói admin đã sửa) và thêm giá model còn thiếu.

### Bảng (xem `pathmind/database/models.py`)
- `subscription_plans` — giá tháng/năm, token/ngày, token/tháng, dung lượng,
  kích thước tệp, `allowed_models` (rỗng = không giới hạn), `features`,
  `description`, `sort_order`, `is_active`.
- `subscriptions` — `status`: `active | canceled | expired | replaced`;
  `current_period_end = NULL` nghĩa là không hết hạn (gói free / admin cấp vĩnh viễn);
  `cancel_at_period_end`, `billing_interval`, `source` (`register|payment|admin|user|system`).
- `payments` — luôn lưu `amount` theo **USD**, kèm `amount_vnd`;
  `status`: `pending | completed | failed | canceled | expired | refunded`;
  `transaction_id` = mã `DTXXXXXXXXXX` = **nội dung chuyển khoản**.
- `usage_logs` — theo ngày (UTC): token, `credits_used`, `cost_usd`, dung lượng hiện tại.
- `usage_model_logs` — chi tiết theo user/ngày/model (lượt gọi, token, cache, tín dụng, chi phí).
- `model_prices` — bảng giá API (admin sửa được).

### Luồng thanh toán
1. User `POST /api/billing/checkout` → tạo payment **pending** (không kích hoạt gì).
2. User được chuyển sang trang VNPAY (QR / thẻ ATM / thẻ quốc tế) với mã giao dịch = `vnp_TxnRef` (pending hết hạn sau 24h).
3. Payment chuyển `completed` bằng **một** trong ba cách:
   - `POST /api/billing/webhook` có header `X-Billing-Signature` = HMAC-SHA256(secret, raw body);
   - admin bấm xác nhận ở `/admin/payments`;
   - `POST /api/billing/payments/{tx}/simulate` (chỉ khi demo mode).
4. `quota_service.complete_payment()` → `activate_subscription()`:
   cùng gói còn hạn ⇒ cộng thêm 30/365 ngày; khác gói ⇒ gói cũ `replaced`, gói mới bắt đầu.

### Enforcement
- `runtime/orchestrator.py`: trước mỗi turn gọi `check_turn_allowed()` (model user chọn,
  hoặc model mặc định của deployment qua `default_llm_model_name()`). Lỗi DB ⇒ fail-open + `logger.warning`.
- So khớp model: khớp chính xác, bỏ tiền tố (`models/…`), chấp nhận hậu tố phiên bản
  (`-20241022`, `-preview`, `-latest`) hoặc alias trong bảng giá (`deepseek-chat` → `deepseek-v4-flash`);
  `gpt-4o` **không** mở `gpt-4o-mini`.
- **Gói = quyền dùng model.** `multi_user/model_access.redacted_model_access` gộp 3 nguồn:
  `plan` (model trong catalog admin khớp `allowed_models` của gói, qua `quota_service.plan_allows_model`),
  `admin` (grant gán tay) và `personal` (đăng nhập riêng). Vì thế gán/mua gói là mở khoá ngay
  danh sách model, cổng capability (hết "Feature locked") và kiểm tra `llm_selection` của Partner.
  Model chỉ hiện nếu **có trong catalog** (Settings → Models) — gói liệt kê model chưa cấu hình thì không có tác dụng.
  Profile owner-bound (Codex/CodeBuddy) không bao giờ mở qua gói.

### Quyền theo gói (entitlements, 2026-09-29)
- `services/entitlements.py` + cột JSON `subscription_plans.entitlements` (admin sửa ở `/admin/plans` →
  "What this plan unlocks"; `null`/ô trống = không giới hạn, `0` = không có). Seed điền mặc định khi cột còn NULL.
- Gói: **Free** (0$), **Plus** (4,99$/tháng, 49$/năm, 11M tín dụng/tháng, 1M/ngày, 2 GB, tệp 25 MB, 8 model),
  **Pro** (9,99$), **Unlimited Campus** (`enterprise`, 29,99$).

  | | Free | Plus | Pro | Unlimited |
  |---|---|---|---|---|
  | Practice, lịch sử chat, Knowledge Center | ✅ | ✅ | ✅ | ✅ |
  | Mastery Path mới | 1 (dùng thử, cả đời) | 10/tháng | ∞ | ∞ |
  | Book mới | 1 (dùng thử, cả đời) | 10/tháng | ∞ | ∞ |
  | Reading, Watching | ❌ | ✅ | ✅ | ✅ |
  | Notebooks, Question Bank, Skills (Learning Space) | ❌ | ✅ | ✅ | ✅ |
  | Community | ❌ | ✅ | ✅ | ✅ |
  | Skill của riêng mình (tạo/tải lên/thêm từ Community, EduHub; built-in không tính) | 0 | 5 | 20 | ∞ |
  | Partner tự tạo | 1 | 3 | 10 | ∞ |

- `allowance_period`: `month` (đếm theo tháng UTC) hoặc `lifetime` (Free: tổng mọi lần tạo, không reset — lỗi `plans.limit_*_trial`). Seed tự chuyển Free bản đầu (0/0) sang 1/1 lifetime. Trang Books / Mastery / thẻ Learning hub hiện "x / giới hạn" (Free: "free trial", Plus: "this month").
- Admin không bao giờ bị giới hạn. Hạ gói **không xoá gì**, chỉ chặn tạo mới; trang đã khoá vẫn giữ dữ liệu.
- Backend trả **402** kèm gợi ý gói rẻ nhất mở được (`plans.*` trong i18n):
  router `community` / `reading` (+ reading_extensions, trừ `GET /workspaces/index` cho nhãn lịch sử chat) /
  `video-learning` gắn dependency trong `main.py`; `notebook_guard` (GET `/api/notebooks` trả `[]` + `locked`),
  `question_bank_guard` (để mở `entries/upsert`, `lookup/by-question`, `GET/PATCH entries/{id}` vì quiz trong chat
  và Practice ghi vào đó). Đếm tháng (UTC) trong bảng `feature_usage` (`check_monthly`/`record_monthly`) ở
  `POST /api/mastery-paths/topics(/draft)` và `POST /api/books` + WS `create`. `check_skill_limit` ở
  `skills/create|install` và `community/skills/{slug}/install`; `check_count_limit("max_partners")` trong `_create_partner`.
- **Đếm lượt tạo trước, hoàn nếu lỗi** (2026-09-30): `reserve_monthly` kiểm tra + cộng 1 lượt trong một bước (khoá `_RESERVE_LOCK`), `release_monthly` trả lại khi tạo book/path thất bại. Trước đây chỉ kiểm tra rồi đếm sau khi tạo xong nên nhiều request song song đều qua cổng (1 lượt dùng thử Free thành nhiều). `POST /topics/draft` vẫn chỉ `check_monthly` (không tốn lượt). Giá trị hạn mức hỏng (`"abc"`, `true`) trong `subscription_plans.entitlements` giữ mặc định thay vì thành "không giới hạn"; lỗi DB khi tra gói ⇒ dùng quyền Free nhưng **không cache** 10 giây.
- **Huy hiệu thông báo Community mở cho mọi gói** (2026-09-30): `community_guard` thay `feature_dependency("community")` cho router Community; riêng `GET /api/community/notifications/unread-count` không bị chặn vì thanh bên poll mỗi 60 giây cho mọi người dùng (trước đây Free nhận 402 liên tục). Test `test_real_app_puts_the_right_guard_on_each_paid_surface` kiểm tra guard thật sự gắn vào app.
- **Courses là tính năng trả phí** (2026-10-01): thêm khoá `courses` vào `FEATURE_KEYS` (Free ❌, Plus/Pro/Unlimited ✅). Router `/api/courses` dùng `courses_guard`: chỉ `GET /api/courses` mở cho mọi gói và trả `{courses: [], locked: true}` khi bị khoá (chat composer, sidebar, Notebooks, Question Bank đều gọi `listCourses`); mọi endpoint khác 402 kèm gợi ý nâng gói. Web: mục **Courses** trong `PRIMARY_NAV` (icon `School`, sau Learning Space, `plan: {feature:'courses'}`), `ROUTE_PLANS` có `/courses`, admin `/admin/plans` có công tắc "Courses", bảng giá thêm dòng "Study courses". Chat đã gắn khoá học cũ vẫn chạy (đọc qua service, không qua router). Hạ gói không xoá khoá học.
- Cache gói theo user 10 giây (`entitlements.invalidate()` khi đổi gói/sửa gói). `GET /api/billing/entitlements`
  trả gói, quyền, hạn mức, mức dùng và `upgrade_to`.
- Frontend: `EntitlementsProvider` (3 layout) + `PlanGate` (map route ở `lib/plan-routes.ts`, gồm
  `/partners/new` khi hết lượt). Mục bị khoá vẫn hiện có ổ khoá và dẫn tới `/pricing` (sidebar Community,
  thẻ Learning hub, ô Learning Space, hộp Save to Notebook / Question Bank / Reading picker / chia sẻ skill);
  `UsageMeter` hiện x/giới hạn (Skills, Partners, lượt Mastery/Book tháng) và nút "Upgrade to …"
  (Plus → Pro, Pro → Unlimited Campus).

---

## 3. API đã có

**User — `/api/billing`** (`api/routers/billing.py`)
| Method | Path | Mô tả |
| --- | --- | --- |
| GET | `/config` | Cổng thanh toán nào đang bật, demo mode, tỉ giá (public) |
| GET | `/plans` | Danh sách gói đang bán (public) |
| GET | `/model-rates` | Hệ số tín dụng từng model (public) |
| GET | `/summary` | Gói, subscription, usage của user |
| POST | `/checkout` | Tạo giao dịch pending `{plan_id, interval, gateway}` |
| GET | `/payments`, `/payments/{tx}` | Lịch sử / trạng thái giao dịch |
| POST | `/payments/{tx}/cancel` | Huỷ giao dịch pending |
| POST | `/payments/{tx}/simulate` | Demo: giả lập đã thanh toán |
| POST | `/cancel`, `/resume` | Tắt / bật lại gia hạn |
| POST | `/upgrade` | **Chỉ** chuyển về gói miễn phí (gói trả phí ⇒ 402) |
| POST | `/webhook` | Callback cổng thanh toán, bắt buộc chữ ký HMAC |

**Admin — `/api/admin`** (`api/routers/admin_dashboard.py`, cả router yêu cầu admin)
| Method | Path | Mô tả |
| --- | --- | --- |
| GET | `/stats` | Tổng user, user trả phí, phân bổ gói, token, doanh thu, pending |
| GET | `/users` | User (từ kho định danh) + gói + usage |
| PUT | `/users/{id}/plan` | Cấp gói `{plan_id, duration_days (0 = vĩnh viễn), interval}` |
| PUT | `/users/{id}/role` | Đổi role (ghi vào kho định danh) |
| POST | `/users/{id}/subscription/cancel` | Huỷ gói trả phí ngay |
| GET | `/users/{id}/subscriptions` | Lịch sử subscription |
| GET/POST | `/plans` | Liệt kê / tạo gói |
| PUT/DELETE | `/plans/{id}` | Sửa / xoá gói (gói đã dùng ⇒ 409, hãy "Ngừng bán") |
| PUT | `/plans/{id}/models` | Sửa danh sách model |
| GET | `/payments?status=` | Danh sách giao dịch |
| POST | `/payments/{tx}/confirm` · `/reject` · `/refund` | Xử lý giao dịch |
| GET | `/available-models` | Gợi ý model (từ bảng giá) |
| GET/POST | `/model-prices` | Bảng giá API model |
| PUT/DELETE | `/model-prices/{id}` | Sửa / xoá giá model (`*` không xoá được) |

**Bạn bè — `/api/friends`** (`api/routers/friends.py`): `GET /list`, `GET /pending` (nhận / đã gửi / đã chặn),
`POST /request {target}`, `POST /respond {request_id, action: accept|reject|block}`,
`DELETE /requests/{id}` (huỷ lời mời đã gửi hoặc bỏ chặn), `DELETE /{friend_user_id}` (huỷ kết bạn + gỡ chia sẻ giữa hai người).

**Xã hội — `/api/social`** (`api/routers/partner_shares.py`, trước đây nằm chung `/api/partners` và dễ trùng route):
`POST /partners/{id}/share`, `GET /partners/shared-with-me`, `GET /partners/{id}/shares`, `DELETE /shares/{id}`,
`POST /rooms`, `GET /rooms`, `GET /rooms/{code}`, `POST /rooms/{code}/join|leave`, `DELETE /rooms/{code}`,
`DELETE /rooms/{code}/members/{user_id}`, `GET /rooms/{code}/messages?after=<id>`, `POST /rooms/{code}/messages {content, ask_partner}`.
Chat phòng: bảng `room_messages` (`RoomMessage`, id tự tăng để poll). `ask_partner` → `send_group_message` (session `room-<id>`, actor = người hỏi, `allow_invoke_other=False`), khoá theo phòng (409 khi Partner đang trả lời), lỗi Partner → dòng `system`.
Thông báo lỗi dùng `services.i18n` khoá `social.*` (en/zh theo ngôn ngữ UI).
Quyền dùng Partner: `multi_user/partner_access.assigned_partner_ids` = grant của admin ∪ `social_partner_ids`
(được chia sẻ + Partner của phòng đang tham gia). Chỉ cấp quyền **dùng**, không bao giờ cấp quyền quản lý.

---

## 4. Frontend (Next.js, thư mục `web/`)

| Route / file | Vai trò |
| --- | --- |
| `app/(settings)/pricing/page.tsx` | Bảng giá, usage, checkout VNPAY (chuyển trang + xác nhận khi quay về), huỷ/bật gia hạn, lịch sử thanh toán |
| `app/(admin)/admin/plans/page.tsx` | Quản lý gói cước |
| `app/(admin)/admin/users/page.tsx` | Quản lý user + cấp gói |
| `app/(admin)/admin/payments/page.tsx` | Duyệt thanh toán |
| `components/admin/AdminTabs.tsx` | Thanh tab dùng chung cho các trang admin |
| `components/admin/ModelPriceTable.tsx` | Bảng giá API model (trong `/admin/plans`) |
| `components/partners/FriendsTab.tsx` | Tab Friends & Study Rooms: tạo/vào phòng, phòng của tôi, kết bạn, lời mời, chặn, Partner được chia sẻ (i18n en/zh) |
| `app/(workspace)/partners/rooms/[code]/page.tsx` | Phòng chat chung + hỏi Partner của phòng |
| `components/partners/SharePartnerModal.tsx` | Chia sẻ Partner cho bạn bè / mở phòng học (từ trang cấu hình Partner) |
| `lib/friends-api.ts` | Client `/api/friends` + `/api/social` |
| `app/(utility)/profile/page.tsx` | Thẻ "Gói cước & Hạn mức" |
| `lib/billing-api.ts` | Client API user + helper format (USD/VND/ngày) |
| `lib/admin-api.ts` | Client API admin |
| `lib/proxy-policy.ts` | `/pricing` được miễn đăng nhập |

---

## 5. Cấu hình

PathMind **bỏ qua file `.env` ở gốc dự án**. Các biến dưới đây
phải là **biến môi trường của tiến trình backend** (VD Windows:
`set BILLING_DEMO_MODE=true` trước khi chạy, hoặc thêm vào `start.bat`). Mẫu và giải thích có trong `.env.example`.

| Biến | Ý nghĩa |
| --- | --- |
| `BILLING_DEMO_MODE` | `true` = hiện cổng demo + nút "Mô phỏng thanh toán" (chỉ dùng khi demo) |
| `BILLING_WEBHOOK_SECRET` | Secret HMAC cho webhook; để trống = tắt webhook |
| `BILLING_USD_VND_RATE` | Tỉ giá hiển thị/QR (mặc định 25400) |
| `BILLING_CREDIT_USD_PER_MTOK` | Giá gốc của 1 tín dụng, USD/1M token (mặc định 0.25) |
| `VNPAY_TMN_CODE`, `VNPAY_HASH_SECRET`, `VNPAY_ENV`, `PATHMIND_PUBLIC_URL` | Cổng VNPAY (đặt trong `local.env.bat`) |
| `DATABASE_URL` | Tuỳ chọn; mặc định SQLite trong `data/user/` |
| `REGISTER_MAX_PER_IP_PER_HOUR` | Số tài khoản tự đăng ký tối đa mỗi IP mỗi giờ (mặc định 5, 0 = tắt) |

Không có cổng nào được cấu hình ⇒ modal thanh toán báo "chưa cấu hình".

---

## 6. Việc còn tồn đọng (ưu tiên từ cao xuống)

1. **Cập nhật bảng giá model định kỳ** (`/admin/plans` → Bảng giá API). Gemini 3.6–3.8 Flash
   tăng giá gấp đôi từ 01/01/2027; model mặc định hiện là `gemini-3.6-flash` (×5.4) — cân nhắc
   đổi sang `gemini-3.1-flash-lite` (×2) hoặc `deepseek-v4-flash` (×1.9) để rẻ hơn.
2. **Phòng học realtime**: hiện mỗi thành viên chat riêng với Partner của phòng; muốn hội thoại chung
   cần nối phòng với `partner_groups`/WebSocket.
3. **Chống spam mạnh hơn**: throttle đăng ký lưu trong bộ nhớ (1 process); nên thêm captcha / xác thực email.
4. **Múi giờ**: hạn mức ngày reset theo 00:00 UTC (7h sáng giờ VN).
   Doanh thu tháng ghi nhận theo ngày thanh toán (gói năm chưa phân bổ 12 tháng).
5. **i18n**: chuỗi tiếng Việt đang hard-code trong các trang mới — `npm run i18n:check` có thể báo lỗi.
6. **Contracts**: chạy `npm run contracts:generate` để cập nhật `web/contracts` sau khi bỏ endpoint cũ.
7. Chuyển VNPAY sang production (hợp đồng merchant VNPAY, `VNPAY_ENV=production`, khai báo IPN URL HTTPS).

Chạy: `start.bat` (production, mượt) / `start.bat dev` (dev server) / `stop.bat` ở thư mục gốc (log ở `logs\backend.log`, `logs\frontend.log`).

---

## 7. Quy ước khi sửa code

- Datetime trong DB là **UTC naive** — dùng `quota_service._utcnow()`.
- Thêm cột mới: khai báo `nullable=True` trong `models.py`; `init_database()` tự
  `ALTER TABLE ADD COLUMN` cho cột thiếu. Thay đổi phức tạp hơn ⇒ dùng Alembic.
- Mọi logic gói/usage đặt ở `services/quota_service.py`; router chỉ gọi lại.
- Không bao giờ kích hoạt gói trả phí ngoài `complete_payment()` hoặc admin cấp.
- Không tin `user_id`/`plan_id` từ client hay webhook — lấy từ payment trong DB.
- Frontend: dùng biến theme `var(--background)`, `var(--card)`, `var(--border)`…
  (không hard-code màu tối); **không dùng `window.confirm/alert/prompt`** — dùng
  `confirmAction()` (`lib/confirm.ts`) hoặc `ConfirmDialog`, và toast `notify`.
- Một số file frontend dùng **CRLF** (`admin/users/page.tsx`, `profile/page.tsx`,
  `lib/admin-api.ts`); giữ nguyên kiểu xuống dòng để diff sạch. File `.py` luôn LF.
- `lib/` không được import từ `app/`, `components/` (luật dependency-cruiser).

## 8. Kiểm thử

Bộ test không nằm trong repo: ở `C:\PathMind_removed\tests\tests` (đã đổi tên sang `pathmind`; có thêm
`multi_user/test_signup_flow.py`, `multi_user/test_account_deletion.py`, `services/test_security_hardening.py`,
`services/test_plan_default_model.py`). Script chạy toàn bộ `fulltest.bat` đã chuyển sang
`C:\PathMind_removed\cleanup_20260925\` — nó chép test vào `tests/`, đặt `DATABASE_URL` sang SQLite tạm
(để **không ghi vào DB thật**), chạy pytest (log `logs/pytest_full2.log`) rồi chuyển test về chỗ cũ.

Test gói cước: `tests/services/test_entitlements.py` (quyền, dùng thử Free, reserve/release, gợi ý nâng gói, skill/partner, guard). Các test API cũ chạy với người dùng "gói mở hết" nhờ plugin pytest `C:\PathMind_removed\tests\tests\plan_unlimited.py` (`PYTHONPATH=tests python -m pytest -p plan_unlimited …`; bỏ qua file test_entitlements).

Kết quả lần chạy 2026-09-25: **6004 pass / 113 fail / 66 skip**. Các lỗi còn lại đều do môi
trường, không phải do code: sandbox/bwrap/symlink/quyền "owner-only" chỉ chạy trên Linux/macOS;
test của phần đã gỡ (CLI `pathmind_cli`, Docker, `services/subagent`, kênh Telegram/Slack/Weixin
chưa cài thư viện); và test API trả 401 vì chạy trong thư mục dự án đang bật đăng nhập
(`data/user/settings/auth.json`).

```bash
# Frontend
cd web
npm run typecheck
npm run lint
npm run i18n:check
```

Kiểm tra tay nhanh: bật `BILLING_DEMO_MODE=true` → đăng ký user mới → `/pricing`
→ nâng cấp Pro → "Mô phỏng thanh toán" → xem gói ở `/profile` và
`/admin/users`, giao dịch ở `/admin/payments`.
