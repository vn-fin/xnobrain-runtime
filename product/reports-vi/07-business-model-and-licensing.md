# 07 · Mô hình kinh doanh & Cấp phép

> Kế hoạch — mã nguồn mở hóa một bản cá nhân, bán một bản doanh nghiệp/đám mây độc quyền
> mà các công ty có thể tự lưu trữ (self-host) hoặc chạy trên đám mây với mã nguồn được
> bảo vệ — là một mô hình **open-core (lõi mở)** đã được kiểm chứng. File này đối chiếu
> các tiền lệ, rồi đưa ra các khuyến nghị cấp phép cụ thể. Các tuyên bố bên ngoài đều có
> dẫn nguồn; các khuyến nghị được đánh dấu là ý kiến.

## 1. Các tiền lệ open-core (mô hình đã được đi mòn lối)

Mô hình open-core = **lõi mã nguồn mở (MIT/Apache) + tính năng doanh nghiệp độc quyền
qua đăng ký trả phí**
[[tổng quan open-core](https://viprasol.com/blog/open-source-business-model/)]. Việc bạn
tách `brain4all` (công khai) + `brain4all-enterprise` (riêng tư) là bài bản sách giáo khoa.

| Công ty | Giấy phép lõi | Mô hình thương mại | Bài học cho bạn |
|---|---|---|---|
| **GitLab** | khởi đầu MIT, Enterprise Edition độc quyền | open-core; CE miễn phí, EE trả phí cho bảo mật/tuân thủ/DevOps | thành công open-core kinh điển; phân tầng tính năng hiệu quả |
| **HashiCorp** | chuyển MPL → **BSL/BUSL** (2023) | nguồn-có-sẵn (source-available); chặn đối thủ bán lại dưới dạng dịch vụ quản lý | [[InfoQ](https://www.infoq.com/news/2023/08/hashicorp-adopts-bsl/)] — BSL chặn các hyperscaler, nhưng cộng đồng đã fork ra **OpenTofu** — phản ứng dữ dội từ cộng đồng là có thật |
| **Elastic** | Apache → **SSPL** → quay lại AGPL | nguồn-có-sẵn (source-available) rồi mở lại | siết quá chặt có thể phải trả giá bằng thiện chí của cộng đồng |
| **n8n** | **fair-code** ("dùng nội bộ miễn phí, cấm bán lại") | open-core + đám mây | gần với hình dạng của bạn nhất; cho các công ty tự lưu trữ (self-host) miễn phí, chặn bán lại |
| **Sourcegraph** | chuyển sang đóng mã nguồn | bán hàng doanh nghiệp | thuần-doanh-nghiệp là khả thi nhưng mất đi phễu (thu hút người dùng) từ OSS |

**Mô hình phù hợp với Brain4All nhất:** **lõi dễ dãi (permissive)/fair-code + control
plane (mặt điều khiển) doanh nghiệp đóng** (như n8n / GitLab), *chứ không phải* đổi giấy
phép lõi theo hướng hạn chế (dù sao bạn cũng không sở hữu giấy phép của Hermes — nó là
MIT và vẫn là MIT).

## 2. Hai quyết định cấp phép mà bạn thực sự phải đối mặt

### Quyết định A — giấy phép cho *chính* kho OSS của bạn (`brain4all`)
README nói thẳng ra: *"Chọn và thêm một giấy phép trước khi phân phối công khai."*
Hiện tại giấy phép này **chưa được đặt** và đang chặn việc ra mắt công khai. Các phương án (ý kiến):

| Phương án | Tác động | Khi nào nên chọn |
|---|---|---|
| **MIT / Apache-2.0** | mở tối đa; bất kỳ ai (kể cả đối thủ) đều có thể dùng/lưu trữ | nếu độ chấp nhận/phễu > sự bảo vệ; khớp với MIT của Hermes; đơn giản nhất |
| **AGPL-3.0** | copyleft; bất kỳ ai cung cấp nó dưới dạng *dịch vụ mạng* đều phải mở các sửa đổi của họ | ngăn một hyperscaler chạy một fork lưu trữ đóng, mà vẫn là "mã nguồn mở" |
| **BSL / fair-code (kiểu n8n)** | nguồn-có-sẵn (source-available); tự lưu trữ (self-host) miễn phí, **cấm bán lại/dịch vụ quản lý** trong N năm rồi chuyển đổi | bảo vệ *thương mại* tốt nhất; **nhưng không phải "mã nguồn mở" theo OSI** — cần quản lý cách truyền thông |

**Khuyến nghị (ý kiến):** **Apache-2.0 hoặc AGPL-3.0 cho lõi OSS**, và giữ **toàn bộ
lợi thế phòng thủ (moat) (kiểm định, registry, danh tính, royalty, thanh toán) trong kho
đóng `brain4all-enterprise`.** Lý do:

- Lợi thế phòng thủ (moat) nằm ở **các engine 05–07 & 09**, vốn *đã* được thiết kế riêng
  tư từ đầu — nên bạn không cần một giấy phép lõi hạn chế để bảo vệ phần có giá trị. Điều
  đó cho phép lõi giữ được sự mở thực sự (độ chấp nhận, cộng đồng, niềm tin tốt hơn),
  trong khi doanh thu nằm sau ranh giới doanh nghiệp.
- **AGPL** thêm một rào cản nhẹ chống lại một bản sao lưu trữ đóng của lõi mà không làm
  các công ty tự lưu trữ (self-host) e ngại (họ dùng nó nội bộ, không tái phân phối).
- Tránh BSL/SSPL trừ khi bạn thực sự lo sợ một hyperscaler bán lại *lõi* — và lưu ý rằng
  HashiCorp/Elastic cho thấy cái giá phải trả với cộng đồng.
- ⚠️ **Tương thích giấy phép:** nếu bạn chọn AGPL cho lõi, hãy xác nhận tính tương thích
  với Hermes (MIT — ổn, MIT tương thích với AGPL) và mọi phụ thuộc đi kèm, đồng thời duy
  trì một danh mục ghi công/NOTICE.

### Quyết định B — cách định khung *cá nhân (Python + Go)* so với *doanh nghiệp (Go)*
Ý định đã nêu mô tả mã nguồn mở là "Python + Go" và doanh nghiệp là "Go." **Kho hiện tại**
là: **OSS = Python + React; Go = chỉ doanh nghiệp** (đường OSS Go/Postgres cũ đã được cho
nghỉ — xem báo cáo hiện trạng §4). Hãy dung hòa một cách có chủ đích:

- **Khuyến nghị (ý kiến): giữ OSS là Python + React; Go ở lại doanh nghiệp.** Đơn giản
  nhất, khớp với code, một ranh giới sạch sẽ. Bỏ "OSS Python + Go" khỏi bài chào hàng trừ
  khi bạn có lý do cụ thể.
- **Phương án thay thế:** nếu bạn thực sự muốn có một bề mặt Go trong OSS (ví dụ một
  daemon/agent/CLI cục bộ nhỏ cho các bản cài đặt biên/tự lưu trữ), hãy giới hạn phạm vi
  chặt chẽ và coi nó như một thành phần OSS *mới* — đừng hồi sinh server Fiber/Postgres đã
  cho nghỉ.

## 3. Mô hình triển khai & doanh thu (khớp với ý định của bạn)

Ý định của bạn — *cài đặt trên server của một công ty, hoặc triển khai trên đám mây, mã
nguồn được bảo vệ* — ánh xạ gọn gàng vào hợp đồng edition hiện có
(`docs/enterprise-extension.md`):

| Edition | Nơi chạy | Data plane | Control plane | Doanh thu |
|---|---|---|---|---|
| **Tự lưu trữ Miễn phí (OSS)** | server/laptop của công ty | file nguyên tử | không có | $0 (phễu) |
| **Doanh nghiệp tự lưu trữ** | server của công ty | file + thực thi cục bộ | **control plane Go riêng tư** (RBAC, SSO, audit, hạn mức (quota), thanh toán) — mã nguồn được bảo vệ | giấy phép/đăng ký |
| **Đám mây (Miễn phí / Pro / Doanh nghiệp)** | đám mây của bạn (Incus/dịch vụ quản lý) | PostgreSQL riêng tư dùng chung | dịch vụ quản lý | ghế + mức dùng |

Các bổ sung thời kỳ Twin-Terminal (Giai đoạn 3+): **phí theo twin đã cài + thanh toán bù
trừ royalty + phí kiểm định** ("phí ghế + phí theo twin + royalty" của tầm nhìn). Hạ tầng
thanh toán/quyền hưởng (entitlement) đã có sẵn trong doanh nghiệp; **lineage & royalty
(engine 09) là phần xây mới hoàn toàn.**

**Bảo vệ mã nguồn khi tự lưu trữ trên server của một công ty:** ràng buộc thật thà — phần
mềm được giao đến máy của khách hàng thì có thể bị soi. Sự bảo vệ thực sự đến từ: (1) giữ
control plane/lợi thế phòng thủ (moat) là một **dịch vụ mà công ty gọi tới**
(`ENTERPRISE_API_URL`) thay vì code họ chạy, khi khả thi; (2) các điều khoản giấy phép +
cấm bán lại kiểu BSL; (3) giao các binary Go đã biên dịch, không phải mã nguồn. Đừng hứa
hẹn bảo vệ mã nguồn "không thể phá vỡ" cho tại chỗ (on-prem) — hãy hứa hẹn *được cấp phép,
được hỗ trợ, và được bảo vệ về mặt pháp lý*.

## 4. Các hành động tiếp theo cụ thể về chủ đề của file này

1. **Chọn và cam kết giấy phép lõi OSS** (khuyến nghị Apache-2.0 hoặc AGPL-3.0) — đây là
   yếu tố chặn ra mắt theo README.
2. **Thêm danh mục NOTICE / giấy phép bên thứ ba** cho Hermes (MIT) + router + các phụ thuộc.
3. **Quyết định câu hỏi chỉ-Python-hay-Python+Go cho OSS** và cập nhật README/bài chào hàng
   để câu chuyện khớp với code.
4. **Soạn thảo EULA doanh nghiệp + các điều khoản cấm bán lại/kiểu BSL** cho các binary
   doanh nghiệp.
5. **Soạn thảo các mẫu hợp đồng quyền sở hữu tay nghề + trách nhiệm về độ trung thành**
   (hai ô đỏ của tầm nhìn — cũng là điều kiện then chốt cho báo cáo lợi thế phòng thủ về
   kiểm định).
