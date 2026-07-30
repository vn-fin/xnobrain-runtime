# Hiện trạng — Brain4All là gì ở thời điểm hiện tại

> Phạm vi: lấy từ chính repository (`AGENTS.md`, `README.md`,
> `docs/architecture.md`, `docs/enterprise-extension.md`,
> `docs/repository-ownership.md`, `docs/project-summary.md`). Không có tuyên bố nào từ
> bên ngoài ở đây — đây là sự thật nền tảng của mã nguồn ở trạng thái ngày 2026-07-24.

## 1. Định nghĩa trong một câu

Brain4All là một **workspace tự lưu trữ (self-host) trên nền React + FastAPI, bao bọc
Hermes Agent mã nguồn mở của Nous Research** và bổ sung một bộ định tuyến LLM đa nhà
cung cấp (được gọi nội bộ là **9router**), đóng gói thành một sản phẩm có thể tải về
kèm một control plane doanh nghiệp độc quyền tùy chọn.

Ở thời điểm hiện tại, nó là một **workspace AI agent / "studio"** — chưa phải là
marketplace, chưa phải là một terminal kiểm định. Tầm nhìn Twin Terminal (xem mục
Tầm nhìn Twin Terminal) là đích đến; tài liệu này là điểm khởi đầu.

## 2. Kiến trúc runtime (như đã xây dựng)

```
browser → Traefik → React UI
                 → FastAPI :8642  (Hermes native routes + Brain4All routes)
                        → services → repositories → atomic profile/config files
                        → integrations → Hermes CLI/core
                        → integrations → 9router :20128 → LLM providers
```

Những sự thật chính rút ra từ `docs/architecture.md`:

- Container runtime khởi động **đúng hai tiến trình**: FastAPI trên `8642` và
  9router trên `20128`. React được phục vụ qua Traefik.
- Đây là một **modular monolith bằng Python** được xếp lớp lên trên ứng dụng FastAPI
  của Hermes CLI gốc. Việc lắp ráp route được tập trung tại `brain4all/routes/setup.py`.
- **Không có database ứng dụng.** Trạng thái là các file nguyên tử (atomic) nằm dưới
  `DATA_DIR`. Hermes giữ `state.db` riêng cục bộ theo profile cho lịch sử phiên native
  (một file thuộc upstream, không phải schema của Brain4All).
- Phân lớp gọn gàng: **handlers** đảm nhiệm chuyển đổi HTTP/SSE, **services** đảm nhiệm
  quy tắc, **repositories** đảm nhiệm file nguyên tử (atomic), **integrations** thích
  ứng Hermes CLI và 9router, **models** là Pydantic.
- **Không có phụ thuộc control plane được quản lý, không đăng nhập, không proxy API**
  trong bản dựng OSS. OpenTelemetry chỉ chạy cục bộ và mặc định tắt.

### Bản đồ mã nguồn (`brain4all/`)

| Lớp | File | Trách nhiệm |
|---|---|---|
| `handlers/` | `api.py` | Chuyển đổi HTTP + SSE |
| `services/` | `kanban.py`, `platform.py` | Quy tắc nghiệp vụ |
| `repositories/` | `files.py` | Lưu trữ file nguyên tử (atomic) |
| `integrations/` | `hermes.py`, `nine_router.py`, `runtime.py`, `kanban.py`, `config.py` | Adapter cho Hermes CLI + 9router |
| `models/` | `api.py` | Hợp đồng Pydantic (điều khiển `/api/brain/swagger_docs`, `/api/brain/openapi.json`) |
| `routes/` | `setup.py` | Điểm lắp ráp route duy nhất |

## 3. Sản phẩm làm được gì hôm nay (ranh giới tính năng)

Theo `docs/project-summary.md` và `README.md`, **phiên bản local/OSS là không giới hạn**
và cung cấp:

- Không giới hạn agent cục bộ, profile được đặt tên, prompt/cấu hình
- Skill (kỹ năng) (`skills/<skill-id>/SKILL.md`), bộ nhớ, MCP server
- Hội thoại + các lần chạy streaming SSE, cổng phê duyệt (approval core của Hermes)
- Cron cục bộ, nhà cung cấp, team, file workspace
- Ảnh chụp (snapshot) bất biến + gói profile `.zip` di động
- Một React UI bao gồm **bảng Kanban** để lập lịch tác vụ (các commit gần đây cho thấy
  lập lịch Kanban dựa trên DB và sinh tác vụ định kỳ, một API event-stream, và một
  trình soạn thảo tác vụ có quản lý skill)

**Quy tắc lưu trữ & an toàn** (`AGENTS.md`, `README.md`):

- Dữ liệu agent nằm dưới `DATA_DIR/profiles/<agent-id>/`.
- Skill chỉ nằm dưới `.../skills/<skill-id>/SKILL.md`.
- Mọi mutation có cam kết lưu trữ đều ghi một **ảnh chụp (snapshot) bất biến** trước,
  rồi mới đến trạng thái có thể thay đổi qua temp-file → fsync → rename.
- Thông tin xác thực/prompt/tham số tool **không bao giờ** được log, trace, hay đưa vào
  gói di động hoặc telemetry.

## 4. Phân tách open-core (đã thiết kế, xây dựng một phần)

Đây là điều quan trọng chiến lược nhất đã có sẵn trong repo — **mô hình hai repository**
được đặc tả trong `docs/enterprise-extension.md` và `docs/repository-ownership.md`.

| Repo | Dựng | Sở hữu |
|---|---|---|
| `brain4all` (repo này, **công khai/OSS**) | Image React + image FastAPI/Hermes/9router hợp nhất | Cô lập profile, đường dẫn an toàn, snapshot, hội thoại, gọi Hermes, ủy thác 9router, middleware quota, thực thi cấp service |
| `brain4all-enterprise` (**riêng tư**) | Enterprise API + đóng gói cloud được quản lý/Incus | Auth, phân giải tenant/plan, quyền lợi thanh toán, quota phân tán, RBAC, audit, quản lý secret, điều phối được quản lý, lưu giữ telemetry |

Các quy tắc bảo vệ mô hình:

- Repo OSS phải luôn là một **sản phẩm tải về hoàn chỉnh** — file Compose của nó chỉ
  kéo image công khai và **không bao giờ** yêu cầu image riêng tư hay tài khoản cloud.
- **Phụ thuộc là một chiều**: enterprise có thể kéo image runtime công khai; bản triển
  khai OSS không bao giờ kéo image enterprise.
- Enterprise là một **thành phần riêng tư mỏng** (thin private composition), không phải
  fork. Nó ghim một module Brain4All + image runtime đã phát hành và hiện thực một hợp
  đồng `pkg/edition.Policy` (`-1` = không giới hạn).
- Các phiên bản: **self-hosted Free** (chỉ file), **Cloud Free / Personal Pro /
  Enterprise** (control plane PostgreSQL riêng tư dùng chung). Enterprise bổ sung
  tổ chức, RBAC, SSO, audit, quyền lợi theo hợp đồng.
- **Ghi chú lịch sử:** một kế hoạch multi-server Go/Fiber + PostgreSQL trước đây đã bị
  **loại bỏ** (`docs/implementation/README.md`); công việc OSS hiện tại nhắm đến gói
  Python đơn nhất. Câu chuyện Go giờ nằm ở phía **control plane enterprise**.

### ⚠️ Kiểm tra thực tế về ngôn ngữ so với kế hoạch đã nêu

Ý định đã nêu là mã nguồn mở một phần của dự án (mô tả là "Python + Go") và giữ một
phiên bản enterprise bằng Go. Thực tế hiện tại của repository là:

- **OSS = Python** (monolith FastAPI) + frontend React/TypeScript. Con đường Go/Postgres
  đã bị loại bỏ một cách rõ ràng trong repo OSS.
- **Enterprise = Go** (control plane `pkg/edition.Policy`, đường dẫn module
  `github.com/vn-fin/brain4all/`, điều phối được quản lý).

Vậy nên "mã nguồn mở Python + Go" **không** phải là điều mã nguồn làm hôm nay. Hãy
quyết định một cách có chủ đích (xem tài liệu roadmap): hoặc (a) giữ OSS chỉ Python và
Go hoàn toàn thuộc enterprise, hoặc (b) tái đưa một thành phần Go vào OSS (ví dụ một
daemon/CLI cục bộ nhẹ) nếu bạn thực sự muốn một bề mặt Go trong OSS. Câu chuyện gọn
gàng nhất là **(a)**.

## 5. Đánh giá trung thực về khoảng cách tới tầm nhìn

| Tầm nhìn cần (Twin Terminal) | Đã tồn tại hôm nay? |
|---|---|
| Runtime agent, bộ nhớ, skill, MCP, sandbox | ✅ qua Hermes |
| Định tuyến đa nhà cung cấp | ✅ qua 9router |
| Self-host + cloud + mã nguồn enterprise được bảo vệ | ✅ đã thiết kế (phân tách open-core) |
| **Xưởng biên soạn** skill/twin | 🟡 một phần (skill + trình soạn thảo Kanban) |
| **Cổng kiểm định độ trung thành** (moat) | ❌ chưa bắt đầu |
| Danh tính / chống mạo danh | ❌ chưa bắt đầu |
| Kệ (registry) với huy hiệu có hạn dùng | ❌ chưa bắt đầu |
| Lineage, royalty (tiền bản quyền), thanh toán liên tổ chức | ❌ đã có thanh toán enterprise; royalty/lineage thì chưa |
| Giám sát trôi dạt (drift) → tái kiểm định | ❌ chưa bắt đầu |

**Điểm mấu chốt:** các động cơ "hàng hóa phổ thông" (bộ não, thực thi, kết nối, và phần
lớn điều phối) phần lớn được **thuê hoặc đã xây dựng sẵn**. Mọi động cơ mà tầm nhìn đánh
dấu là *lợi thế phòng thủ (moat)* — **kiểm định, danh tính, vòng lặp cải tiến,
thương mại/royalty** — đều là mảnh đất trống. Đó chính là nơi đáng để đầu tư, và là nơi
roadmap nên tập trung.
