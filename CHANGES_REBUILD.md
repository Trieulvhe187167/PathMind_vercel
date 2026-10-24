# Thay đổi áp dụng D1–D10 lên bản cũ (PathMind chưa build lại)

Bản sao lưu trước khi sửa: `scratchpad/new_backup.tgz`. Chưa chạy được test/typecheck (không cài được thư viện: PyPI/npm bị chặn trong môi trường này); chỉ kiểm bằng biên dịch Python (`compileall`) và quét import trỏ tới module đã xóa (không còn lỗi).

## Bước 1 — Gỡ MCP, CLI apps, kênh IM (xong)

### 1a. Kênh IM (chỉ giữ email)
- Xóa 16 module kênh: dingtalk, discord, feishu, matrix, mattermost, mochat, msteams, napcat, qq, slack, telegram, wecom, weixin, weixin_qr, whatsapp, zulip. Giữ base, manager, registry, email.
- Xóa `services/partners/channel_onboarding.py`, `weixin_onboarding.py`, `channel_state_migration.py`.
- `api/routers/partners.py`: bỏ route QR WeChat (`/channels/weixin/qr...`), 4 route `channel-onboarding`, khối vẽ QR ở `/channels/status`, import và model liên quan.
- `services/partners/manager.py`: `_rehome_shared_channel_state` thành no-op. `services/partners/sessions.py`: bỏ bảng phân loại hội thoại Feishu/Slack/Mattermost.
- Web: bỏ tab "Channels" ở trang Partner; xóa `PartnerChannels`, `WeixinQrLogin`, `ChannelOnboardingPanel`, `ChannelRuntimeStatus`, `schema-form`; bỏ các hàm tương ứng trong `lib/partners-api.ts`.

### 1b. CLI apps
- Xóa `services/cli_apps/` và `api/routers/space_cli_apps.py`; bỏ router khỏi `api/main.py`.
- `runtime/providers/view.py`: bỏ nhánh công cụ CLI. `agents/_shared/tool_runtime.py`: bỏ tiền tố CLI. `multi_user/tool_access.py` + `grants.py`: bỏ `allowed_cli_apps` và trường `cli_apps`. `account_deletion.py`: bỏ thư mục `user-cli-apps`. `services/i18n.py`: bỏ khóa `cli_apps.*`.
- Web: xóa trang `/space/cli-apps`, `components/cli-apps`, `lib/cli-apps-api.ts`; bỏ thẻ CLI Apps ở Learning Space.

### 1c. MCP
- Xóa `services/mcp/` (giữ lại `network.py` thành `services/url_guard.py` cho WeKnora; `session_state.py` chuyển thành `runtime/registry/session_state.py`), `api/routers/mcp_settings.py`, `api/routers/space_mcp.py`, `runtime/providers/authorize.py`.
- `runtime/providers/view.py` viết lại: không còn khởi động/hỏi MCP; công cụ trì hoãn chỉ còn pool của registry + công cụ gắn theo tài nguyên.
- `api/main.py`: bỏ router MCP và bước đóng kết nối MCP khi tắt. `api/utils/tool_options.py`: bỏ khởi động MCP. `services/workspace/resources.py`: danh mục MCP trả `[]`.
- Web: xóa trang `/space/mcp`, `components/mcp`, `McpStoreSection`, `useMcpServers`, `lib/mcp-api.ts`, `lib/mcp-store.ts`; bỏ thẻ MCP ở Learning Space và liên kết MCP ở workspace.

### Còn sót (cố ý, vô hại, sẽ dọn sau nếu bạn muốn)
- Tên trường `mcp_tools` trong grant/partner config, khóa `mcp` rỗng trong chính sách workspace, `McpToolGroups` (nhóm công cụ), gói `mcp` trong requirements, chuỗi dịch trong `locales`, `brand-slugs` của CLI, đoạn nhắc trong CLAUDE.md.

### Giữ nguyên theo yêu cầu
- Personas và Skills (kể cả Skill Hub).

## Bước 2 — D3, D7, D8 (xong, thay đổi nhỏ vì bản cũ đã gần đúng)

- **D7 (mastery path):** bản cũ đã gọi là Mastery Path (router `mastery_path`, trang `/learning/mastery`). Chỉ đổi chữ "learning path" thành "mastery path" trong thông báo hạn mức/gói ở `services/i18n.py` (en, và zh: 学习路径 thành 掌握路径) và mô tả gói Plus trong `database/init_db.py`. Gói đã có sẵn trong DB giữ mô tả cũ (seed chỉ điền khi trống). Không đổi: chế độ Deep Research "Learning Path" (tính năng khác).
- **D8 (USD):** không cần sửa. Trang giá hiển thị USD, VND chỉ là "≈" và ở bước thanh toán VNPAY; `payments.amount` lưu USD kèm `amount_vnd`.
- **D3 (điểm thay tiền mặt):** bản cũ không có thưởng hay tiền mặt (không có bảng/route nào), nên không có gì để sửa. Hệ thống điểm thưởng và đổi điểm sẽ được thêm ở Bước 3 cùng R&D.

## Bước 3 — Module 1: vai trò R&D / Educator (xong)

Quyết định: **Educator không cần admin duyệt**. Việc dùng nhánh tri thức và đóng góp được khóa bằng **gói trả phí** (quyền `contribute`), nên không cần bảng yêu cầu vai trò hay UC "Approve Educator Role Request" (UC91 trong danh sách UC mới sẽ bỏ).

Backend
- Vai trò mở rộng từ `admin | user` thành `admin | rnd | educator | user` (`user` = learner): `multi_user/models.py` (thêm `is_rnd`, `is_educator`), `multi_user/identity.py`, `multi_user/context.py`, `services/auth.py`, `services/partners/interaction.py`, `services/quota_service.py`, `database/maintenance.py`, `api/routers/auth.py` (SetRoleRequest), `api/routers/admin_dashboard.py` (UpdateUserRoleBody).
- `api/routers/auth.py`: `SignupRequest.role` (`user` hoặc `educator`; người đầu tiên vẫn là admin); endpoint mới `POST /api/auth/role/educator` (learner tự nâng lên Educator, cấp lại cookie); dependency mới `require_educator`, `require_rnd` (admin cũng qua; đọc vai trò từ kho danh tính nên không bị token cũ làm sai); `/status` trả vai trò thật.
- `services/entitlements.py`: quyền mới `contribute` (Free tắt, Plus/Pro/Unlimited bật, admin sửa được ở `/admin/plans`); R&D không bị giới hạn hạn mức như admin (`current_entitlements`, `quota_service._is_admin`).
- `services/i18n.py`: thông báo `plans.feature_contribute`, `auth.educator_only`, `auth.rnd_only` (en, zh).

Web
- Đăng ký có ô "I am an Educator" (`AuthPortal.tsx`, `lib/auth.ts`).
- `/admin/users`: ô chọn vai trò Learner / Educator / R&D cho mỗi tài khoản (không đụng admin và chính mình).
- `/admin/plans`: công tắc "Knowledge branch & contributions". `useAuthStatus` thêm `isRnd`, `isEducator`; kiểu `UserRole` trong `lib/admin-api.ts`.

Chưa làm trong module này (thuộc các module sau): mời tài khoản R&D qua email, giao diện riêng cho R&D/Educator, và các chức năng nhánh/đóng góp dùng `require_educator` + `contribute`.

## Bước 3 — Module 2: cây tri thức chính (R&D) + nhánh cá nhân (xong, thiết kế chức năng trước)

Theo yêu cầu: làm chức năng trước, tối ưu cấu trúc DB ở lượt sau (các bảng dưới đây là bản đầu, chưa chuẩn hóa/đánh chỉ mục kỹ).

Backend
- `database/models.py`: bảng mới `personal_branches` (một nhánh mỗi người, có `version` để phát hiện sửa chồng), `branch_nodes` (cây nút, `source_kb`/`source_doc`, `prerequisites` JSON), `branch_versions` (ảnh chụp nhánh), `audit_logs` (nhật ký thao tác nhạy cảm). Tự tạo khi khởi động (`create_all`).
- `services/audit.py` mới: `record(...)`, `list_recent(...)`.
- `services/knowledge_branch/` mới: lấy/tạo nhánh, cây, thêm/sửa/di chuyển/xóa nút (chặn vòng lặp, giới hạn 2000 nút, kiểm tra `base_version` ⇒ 409), đặt nút tiên quyết (chặn vòng), lưu/liệt kê/khôi phục phiên bản (tối đa 30).
- `api/routers/knowledge_branch.py` mới, gắn tại `/api/knowledge-branch`, chỉ cho gói có quyền `contribute` (402 nếu Free). Có: `GET ""`, `POST /nodes`, `PATCH|DELETE /nodes/{id}`, `POST /nodes/{id}/move`, `PUT /nodes/{id}/prerequisites`, `GET|POST /versions`, `POST /versions/{id}/restore`.
- Cây chính (`services/knowledge_tree`, `api/routers/knowledge_tree.py`): quyền sửa chuyển từ chỉ admin sang `admin` + `rnd`; các route đọc (`domains`, `tree`, `nodes`, `search`) chỉ R&D/admin (403 với người khác). Công cụ AI vẫn đọc cây qua dịch vụ nên chat không đổi.

Web
- Trang `/knowledge-branch` ("My Knowledge"): xem cây nhánh, thêm/sửa/xóa nút, lưu và khôi phục phiên bản. Mục sidebar "My Knowledge" (khóa theo gói `contribute`, có ổ khóa nâng cấp) và `lib/plan-routes.ts`.
- Mục sidebar "Knowledge Tree" chỉ hiện cho admin/R&D (`roles` mới trong `nav-entries.ts`, lọc ở `SidebarNav.tsx`).
- `lib/knowledge-branch-api.ts` mới.

Còn mở (module sau)
- R&D chưa liên kết được KB GraphRAG vào domain vì `admin_kb_names()` vẫn đọc kho KB của admin ⇒ xử lý trong Module 3 (kho tài liệu của R&D + tải lên/xuống).
- Chưa có giao diện đặt nút tiên quyết, chưa nối nhánh vào truy hồi của AI (Module 4), chưa có đề xuất nhánh/duyệt/hợp nhất/thưởng (Module 5).

## Thiết kế lớp phủ ngầm (bổ sung cho Module 2)
- `database/models.py`: `BranchNode` thêm `anchor_node_id` (gắn vào node cây chính) và `origin` (`auto`|`manual`); `KnowledgeSource` thêm `blob_path`, `content_type` (giữ file gốc để tải về).
- `services/knowledge_branch/service.py`: `create_node` nhận `anchor_node_id`, `origin`.
- `api/main.py`: router `/api/knowledge-branch` bắt buộc `require_educator` + gói trả phí `contribute`.
- `web nav-entries.ts`: "My Knowledge" chỉ hiện cho `admin`, `educator`. Người học thường không xem được cây của mình.

## Module 3 – Kho tài liệu R&D (D10)
Backend:
- MỚI `services/knowledge_tree/store.py`: `list_documents`, `upload_document` (kiểm tra loại file, chống trùng sha256), `read_document` (tải file gốc), `delete_document` (chặn nếu node còn trỏ tới), `export_tree`/`import_tree` (JSON `pathmind-knowledge-tree` v1, tối đa 5000 node, tạo domain mới, bỏ refs).
- `services/knowledge_tree/service.py` `import_file`: lưu file gốc + ghi audit `store.document.upload`.
- `services/knowledge_tree/__init__.py`: xuất `store`.
- `api/routers/knowledge_tree.py` thêm: `GET /documents`, `POST /domains/{id}/documents`, `GET /documents/{id}/download`, `DELETE /documents/{id}`, `GET /domains/{id}/export`, `POST /domains/import-tree`, `GET /audit` (giới hạn 25 MB/file).
- Audit action: `store.document.upload|download|delete`, `store.tree.export|import`. Chỉ admin/R&D truy cập.
Web:
- `lib/knowledge-tree-api.ts`: API tài liệu, export/import cây, audit, tải file có xác thực.
- `KnowledgeTreePage.tsx`: khung "Document store" (tải lên/tải về/xóa, xuất/nhập cây, xem activity log).
Còn lại của R&D: liên kết KB (hiện `admin_kb_names` vẫn đọc thư viện KB của admin).
Kiểm tra: `compileall` OK, `chk.py` chỉ còn cảnh báo cũ `pathmind_web`; không chạy được test/tsc (không có pip/npm).

## Module 4 – Lớp phủ cá nhân nối vào truy hồi AI (D9)
Backend:
- MỚI `services/knowledge_branch/overlay.py`: `enrich_from_document` (tạo nút từ mục lục tài liệu, gắn `anchor_node_id` vào node cây chính khớp nhất, `origin=auto`; không dùng LLM nên không tốn credit), `reset` (lưu version "Before reset" rồi xóa, ghi audit `branch.reset`), `search`, `has_nodes`.
- `services/knowledge_tree/unified.py` (`knowledge_search`): thêm mục "Personal notes" từ lớp phủ của người đang hỏi; bỏ nút trùng tiêu đề với nút cây chính (cây chính thắng); nguồn mới `type: personal_branch`.
- `agents/_shared/tool_composition.py`: công cụ `knowledge_search` bật khi cây chính có dữ liệu hoặc lớp phủ của user có nút.
- `api/routers/knowledge_branch.py`: `POST /enrich` (upload, ≤25 MB), `POST /reset`.
Web: nút "Add document" và "Reset" ở trang My Knowledge; `lib/knowledge-branch-api.ts` thêm `enrichBranch`, `resetBranch`.
Chưa làm: tự làm giàu khi người dùng thêm tài liệu vào KB của họ (hiện bấm "Add document"), lớp phủ cho người học thường (hiện chỉ Educator), UI đặt tiên quyết, đường học (mastery path) trên cây.

## Module 5 – Đề xuất nhánh, điểm thưởng, System Status, console R&D/Admin
Backend:
- `database/models.py`: bảng mới `branch_recommendations`, `reward_ledger` (sổ điểm, chỉ ghi thêm), `reward_rules`, `bonus_credits`.
- MỚI `services/recommendations.py`: Educator đề xuất cây con của nhánh mình (chụp nút + giữ file gốc); R&D xem, tải tài liệu (audit), chọn nút để hợp nhất vào cây chính (có thể nhận luôn tài liệu vào kho domain), hoặc từ chối kèm ghi chú. Nhánh của Educator giữ nguyên. Hợp nhất tự cộng điểm.
- MỚI `services/rewards.py`: quy tắc điểm chỉnh được (mặc định 100 điểm/đề xuất + 10/nút; 20 điểm = 1 ngày gói; 1 điểm = 500 credit AI, hạn 30 ngày; tối thiểu 20). Đổi điểm lấy ngày gói (kéo dài gói đang dùng, hoặc gói trả phí rẻ nhất nếu đang Free) hoặc credit AI. Không có tiền mặt.
- `services/quota_service.py` `check_token_quota`: cộng credit thưởng còn hạn vào hạn mức ngày và tháng.
- `services/knowledge_branch/overlay.py`: `enrich` giữ file gốc của Educator (để đính kèm khi đề xuất).
- MỚI `services/system_status.py`, `api/routers/{recommendations,rewards,system_status}.py`; đăng ký ở `api/main.py`: `/api/recommendations` (Educator gửi; R&D duyệt), `/api/rewards` (người dùng xem/đổi; R&D chỉnh quy tắc, cấp/trừ điểm), `/api/system-status` (R&D/admin, chỉ đọc).
Web (console riêng cho R&D/Admin, tách khỏi khu người học):
- `(admin)/layout.tsx` + `StaffGuard`: chỉ admin/R&D; người khác bị chuyển về /chat.
- `/admin` → `/admin/status`. Tab mới: System Status, Knowledge (cây chính + kho tài liệu), Reviews (duyệt đề xuất), Rewards (quy tắc, cấp điểm, sổ). Tab cũ Plans/Users/Payments/Insights chỉ hiện cho admin (`AdminTabs` lọc theo vai trò).
- Đăng nhập: admin/R&D vào thẳng `/admin` (trừ khi có `?next=`).
- Sidebar người học bỏ "Knowledge Tree"; `/knowledge-tree` chuyển sang `/admin/knowledge`. Cài đặt: lối vào "Admin"/"Staff console" cho cả R&D.
- Trang My Knowledge (Educator): nút "Recommend to R&D" ở nút gốc, khung điểm thưởng (số dư, đổi ngày gói/credit AI, trạng thái đề xuất).
Lưu ý/chưa làm: không thay "System Update" cũ (chưa kiểm tra còn chỗ nào dùng); R&D mời bằng email, Browse gaps, Rate answer, huy hiệu; UI đặt tiên quyết; chuỗi i18n mới chưa thêm bản dịch. Credit thưởng cộng vào hạn mức chứ chưa trừ dần từng lượt (tối ưu DB sau).
Kiểm tra: `compileall` OK, `chk.py` chỉ còn cảnh báo cũ; chưa chạy test/tsc.

Dọn D5: xóa `web/lib/app-update.ts` (không còn nơi dùng, backend không có /api/system/update) và khóa `update-checks` trong `settings-extensions.ts`.

## Module 5b – Mời R&D bằng email, đánh giá câu trả lời AI
- `database/models.py`: bảng mới `invitations`, `answer_ratings`.
- MỚI `services/invites.py`: lời mời có chữ ký (HMAC), hạn 7 ngày, một lần dùng, thu hồi được; mời lại cùng email sẽ thu hồi lời mời cũ.
- `api/routers/auth.py`: `POST /api/auth/invite`, `GET /api/auth/invitations`, `DELETE /api/auth/invitations/{id}` (chỉ admin); `SignupRequest.invite`: đăng ký bằng link mời → vai trò `rnd`, email phải trùng, tài khoản coi như đã xác minh email. Không có SMTP thì trả link để admin tự gửi. Audit: `auth.invite`, `auth.invite.accept`, `auth.invite.revoke`.
- Web: `InviteRndCard` ở `/admin/users` (gửi, xem trạng thái, thu hồi); trang đăng ký đọc `?invite=` (tự điền email, mở tab đăng ký); `lib/auth.ts`, `lib/admin-api.ts`.
- MỚI `api/routers/answer_ratings.py` (`/api/answer-ratings`): đánh dấu đúng/sai kèm lý do (mỗi người một đánh giá cho mỗi tin nhắn), xem theo phiên, R&D xem danh sách "sai" gần đây.
- Web: `features/chat/messages/RateAnswerButtons.tsx` (ngón cái lên/xuống cạnh nút Copy, sai thì hỏi lý do); `ChatMessageList.tsx` gắn nút; trang System Status hiện số câu bị đánh dấu sai 7 ngày và danh sách gần đây (`system_status.py` thêm `answers_wrong_7d`).
Kiểm tra: `compileall` OK; `tsc --noEmit` toàn bộ web sạch lỗi (chạy bằng node_modules của bản cũ, đã gỡ liên kết sau khi chạy). Chưa chạy được server/test backend (thiếu fastapi, không cài được).

## Module 4b – Tiên quyết và đường học (D7)
- `database/models.py`: bảng mới `node_mastery` (nút đã nắm vững, theo người dùng).
- MỚI `services/knowledge_branch/path.py`: `mastery_path` (sắp theo thứ tự học, tiên quyết trước; trạng thái `mastered`/`ready`/`locked`, nút gợi ý tiếp theo, tiến độ), `set_mastered`.
- `api/routers/knowledge_branch.py`: `GET /path`, `PUT /nodes/{id}/mastered`.
- Web (My Knowledge): nút "Prerequisites" ở mỗi nút (chọn các nút phải học trước; chặn vòng ở backend), khung "Mastery path" (thứ tự học, tiến độ x/y, đánh dấu đã nắm; nút bị khóa cho tới khi nắm hết tiên quyết).
- `tsc` web sạch lỗi; `compileall` OK; chưa chạy server.

## Module 6 – Công cụ admin (UC99, UC101, UC102)
- `database/models.py`: bảng mới `system_settings`.
- MỚI `services/system_settings.py`: cài đặt do admin sửa, giá trị trong DB thắng biến môi trường. Hiện có: khóa đăng nhập (số lần sai, số phút khóa) và SMTP (host, port, user, mật khẩu, người gửi, mã hóa). Mật khẩu SMTP không bao giờ trả về giao diện; để trống = giữ nguyên. Audit `settings.update`.
- `api/routers/auth.py`: `_lockout_limits()` đọc cài đặt admin (thay hằng số từ env). `services/email_verification.py`: SMTP đọc từ cài đặt admin trước, env sau.
- MỚI `api/routers/admin_ops.py` (`/api/admin-ops`, chỉ admin): `GET|PUT /settings`, `POST /settings/test-email`, `GET /jobs`, `PUT /jobs/{id}/enabled`, `POST /jobs/{id}/run`, `GET /audit` (lọc theo nhóm hành động, người thực hiện). Audit `job.enable|disable|run`.
- Web (tab chỉ admin): `/admin/settings`, `/admin/jobs` (tự làm mới 15 giây), `/admin/audit`; `lib/admin-ops-api.ts`; `AdminTabs` thêm 3 tab.
Chưa làm: UC100 (storage backends: hiện không có lớp object storage nào trong mã, cần quyết định S3/MinIO/local), đăng nhập Google, mã hóa mật khẩu SMTP trong DB (đang lưu thường), thông báo email khác ngoài xác minh và lời mời.
Kiểm tra: `compileall` OK, `tsc --noEmit` web sạch lỗi; chưa chạy server.

## Module 7 – Quản lý nơi lưu trữ (UC100): thư mục local và S3/MinIO
- `database/models.py`: bảng mới `storage_backends` (cấu hình, mặc định, kết quả thử), `branch_documents` (file gốc của Educator). Cột `blob_path` của kho R&D giờ chứa "tham chiếu lưu trữ" `<backend_id>|<key>` (đường dẫn tuyệt đối cũ vẫn đọc được).
- MỚI `services/storage/object_store.py`: `LocalBackend` (chặn `..`, giới hạn trong thư mục gốc) và `S3Backend` (AWS Signature V4 viết tay, chỉ dùng thư viện chuẩn; path-style cho MinIO hoặc virtual-hosted; tiền tố thư mục); `save/load/remove`, kiểm tra cấu hình, `count_refs`. Chưa có backend mặc định thì dùng thư mục `system/objects`.
- Nối vào: kho tài liệu R&D (`knowledge_tree/store.py`), file gốc Educator (`knowledge_branch/overlay.py` → `branch_documents`), đề xuất (`recommendations.py`, lưu `ref` thay `path`).
- `api/routers/admin_ops.py` (admin): `GET|POST /storage`, `PUT|DELETE /storage/{id}`, `POST /storage/{id}/test` (ghi–đọc–xóa file thử), `POST /storage/{id}/default` (phải thử thành công trước). Không xóa được backend còn file hoặc đang là mặc định; không đổi loại. Khóa bí mật S3 không bao giờ trả về giao diện. Audit `storage.add|edit|test|default|delete`.
- Web: tab `/admin/storage` (thêm/sửa, thử, đặt mặc định, xóa; hiện số file và kết quả thử).
Đã thử: lớp lưu trữ với server S3 giả (ghi/đọc/xóa, chữ ký có mặt) và khóa ký khớp vector chuẩn của AWS. Chưa thử với S3/MinIO thật.
Chưa làm: chuyển file cũ sang backend mới (hiện file ở lại nơi nó được ghi); file đính kèm chat vẫn dùng kho riêng `attachment_store`; mã hóa khóa S3 trong DB (đang lưu thường).
Kiểm tra: `compileall` OK, `tsc --noEmit` web sạch lỗi.

## Module 8 – Email thông báo (UC103)
- MỚI `services/notifications.py`: email là kênh ngoài duy nhất; gửi nền, không làm hỏng luồng chính; không có SMTP thì chỉ ghi log.
- MỚI `services/password_reset.py` + `api/routers/auth.py`: `POST /api/auth/password/forgot` (luôn trả ok, không lộ email nào đã đăng ký, tối đa 3 lần/giờ/email, link hạn 1 giờ) và `POST /api/auth/password/reset` (link ký HMAC, gắn với mật khẩu hiện tại nên dùng một lần; kiểm tra độ mạnh mật khẩu; audit `auth.password_reset`; gửi email báo đã đổi). Không có SMTP: link được ghi vào log máy chủ.
- `services/recommendations.py`: gửi email cho Educator khi đề xuất được hợp nhất (kèm điểm) hoặc bị từ chối (kèm ghi chú).
- Web: liên kết "Forgot password?" ở đăng nhập; trang `/forgot-password` và `/reset-password`; `lib/auth.ts` thêm `requestPasswordReset`, `resetPassword`.
- Email hiện có: xác minh, lời mời R&D, đặt lại mật khẩu, đã đổi mật khẩu, kết quả duyệt đề xuất.
Kiểm tra: `compileall` OK, `tsc --noEmit` web sạch lỗi; chưa chạy server.

## D2 – Stage 1: thu hẹp engine (GraphRAG cho cây chính, LightRAG cho tài liệu người dùng)
- `services/rag/factory.py`: `NEW_KB_PROVIDERS=(graphrag, lightrag)`, `default_provider_for_role` (admin/rnd → graphrag, còn lại → lightrag); danh sách pipeline chỉ còn 2 engine.
- `routers/knowledge.py`: `_new_kb_provider` kiểm tra engine khi tạo KB, cập nhật config, probe/connect-folder (mặc định theo role). Engine khác → 400.
- Các route engine đã bỏ (probe/connect lightrag-server, weknora, list/probe/connect ima, PUT config pageindex/ima/llamaindex/lightrag-server) trả 410.
- `CreateKbModal.tsx`: mặc định "llamaindex" → "lightrag".
- Kiểm tra: compileall OK, names.py/chk.py chỉ còn 3 lỗi cũ (có sẵn ở bản gốc), tsc sạch.
- Chưa làm (Stage 2): xoá code pipeline llamaindex/pageindex/ima/weknora/lightrag_server, UI engine cũ, xử lý KB cũ.

## D2 – Stage 2: xoá hẳn engine cũ (GraphRAG + LightRAG là hai engine duy nhất)
Sao lưu trước khi xoá: `/home/claude/pm/backup/new_before_D2_stage2.tgz` (không gồm node_modules).
**Backend – đã xoá**
- `services/rag/pipelines/{llamaindex,pageindex,ima,weknora,lightrag_server}`, `capabilities/ima` (capability + 5 tool ima_*), `logging/adapters/llamaindex.py`.
- Cài đặt runtime của pageindex/ima/llamaindex/lightrag_server (`services/config/runtime_settings.py`, `__init__`).
- Route: config pageindex/ima/llamaindex/lightrag-server; probe/connect lightrag-server, weknora, ima; list-ima.
- Dependency `pageindex`, `llama-index*` (pyproject + requirements/cli.txt). Giữ faiss-cpu, PyMuPDF.
**Backend – đã sửa**
- `factory.py`: `DEFAULT_PROVIDER="lightrag"`, `KNOWN_PROVIDERS={graphrag,lightrag}`; chuỗi provider cũ → lightrag + `needs_reindex` (KB cũ phải index lại).
- `index_probe`, `preflight`, `linked_kb` (chỉ link graphrag/lightrag), `service`, `embedding_binding`, `doctor`, `knowledge/manager` (bỏ register_ima/weknora/lightrag_server), `manifest`, `deferred_tools`, `builtin_specs`, `capabilities/registry`.
- Agent chat/research/question/co_writer/book/orchestrator: bỏ toàn bộ nhánh PageIndex (tool bundle, note, validate chọn một KB OSS).
- Giữ nguyên hằng `*_KB_TYPE` (ima/weknora/lightrag_server) trong `kb_types.py` để KB kết nối cũ vẫn được nhận diện là "connected".
**Web**
- Xoá: `PageIndexSettingsModal`, `ImaConnectionFields`, `useImaConnection`, `lib/ima-connection`, form LlamaIndex/Ima/LightRAG-Server trong EngineDetail, icon llamaindex/pageindex/ima, API client tương ứng.
- `CreateKbModal` chỉ còn tạo KB graphrag/lightrag + link folder/Obsidian/MarginNote; mặc định lightrag; bỏ tham số pageindexMode.
- Chọn nhiều KB không còn ràng buộc "một KB pageindex-oss".
**Kiểm tra**: compileall OK; names.py/chk.py chỉ còn 3 lỗi cũ; tsc sạch (chưa chạy server/test).
**Còn lại**: chạy `npm run contracts:generate` khi có môi trường (openapi.json/api.ts còn route cũ); dịch/dọn chuỗi i18n không còn dùng; đổi tên biến `pageIndexProvider` trong vài component web.
