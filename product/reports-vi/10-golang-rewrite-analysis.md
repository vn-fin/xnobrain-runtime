# Xây dựng hay Viết lại — Tái hiện thực Hermes bằng Go

Một câu hỏi kiến trúc lặp đi lặp lại là có nên clone runtime agent Hermes và
tái hiện thực nó bằng Go hay không — chỉ giữ lại những tính năng quan trọng, bỏ qua CLI, và
hậu thuẫn bằng PostgreSQL — theo cách mà các dự án cộng đồng đã tái hiện thực OpenClaw bằng Go
(GoClaw, LiteClaw, và tương tự). Phần này trình bày quyết định và lập luận của nó.

## Khuyến nghị

**Việc viết lại toàn bộ Hermes bằng Go là hợp pháp nhưng sai lầm về mặt chiến lược.** Ba lý do,
mỗi lý do tự thân đã đủ:

1. **Vòng lặp agent bị giới hạn bởi độ trễ mô hình — Go về cơ bản không mang lại tăng tốc nào ở đó.**
   Thời gian thực tế (wall-clock) bị chi phối bởi suy luận LLM và I/O công cụ, chứ không phải bởi việc thực thi Python.
2. **Một fork từ bỏ vĩnh viễn tốc độ phát triển thượng nguồn (upstream) của Hermes** (370+ người đóng góp, các bản
   phát hành thường xuyên) **và phá vỡ hệ sinh thái skill/plugin Python** (các hook `SKILL.md` + `plugin.py`
   + chuẩn `agentskills.io`), vốn là một phần lớn giá trị của Hermes.
3. **Mỗi tháng dành để viết lại một runtime hàng hóa (thương phẩm) là một tháng không dành cho
   engine kiểm định** — tài sản duy nhất có thể phòng thủ.

**Giải pháp thay thế được khuyến nghị (hybrid):** giữ runtime Hermes bằng Python như một phụ thuộc
được vendor (đóng gói kèm), được điều phối, đằng sau API riêng của nó. Xây dựng mọi thứ mới — và mọi thứ
thực sự hưởng lợi từ Go và PostgreSQL — trong một lớp riêng biệt: engine kiểm định,
kệ (registry), danh tính, royalty (tiền bản quyền) và dòng dõi (lineage), và control plane (mặt điều khiển) đa người thuê (multi-tenant). Đó là nơi
tính đồng thời của Go và các giao dịch của PostgreSQL phát huy giá trị, và đó là nơi lợi thế phòng thủ (moat)
tồn tại. Nó cũng chính xác, gần như hoàn toàn, là kiến trúc hai kho lưu trữ (repository) đã được đặc tả cho
dự án.

Kết quả: PostgreSQL, Go, một control plane dạng một tệp nhị phân (single-binary), và tính đồng thời đa người thuê (multi-tenant) —
được đặt tại lớp cần đến chúng — mà không cần viết lại agent, đồng thời giữ được
thượng nguồn (upstream) và hệ sinh thái của Hermes một cách miễn phí.

## Tính hợp pháp

Hermes được cấp phép MIT. Nó có thể được fork, tái hiện thực bằng bất kỳ ngôn ngữ nào, giữ dạng đóng, và
thương mại hóa, miễn là bản quyền và thông báo giấy phép được bảo toàn. Không có rào cản pháp lý
nào; quyết định thuần túy là về kỹ thuật và chiến lược.

## Quyết định là về việc dồn nỗ lực khan hiếm vào đâu

Đây không phải là "Go đối đầu Python". Đây là "một đội sáng lập nhỏ dành các tháng của mình vào đâu?"
Có một lợi thế phòng thủ (moat) về kiểm định cần xây dựng và vài đối thủ cạnh tranh đang di chuyển nhanh. Một cuộc viết lại là
một khoản đầu tư nhiều tháng vào một engine **hàng hóa** mà chính tầm nhìn sản phẩm đã đánh dấu là
"không bao giờ là một lợi thế phòng thủ (moat)". Đối với mỗi thành phần, câu hỏi là: việc viết lại nó bằng Go có tạo ra
giá trị có thể phòng thủ, hay chỉ đơn thuần tái tạo một thứ miễn phí vốn tự cải thiện mà không cần đầu tư nào?

## Ba sự thật kỹ thuật

**Vòng lặp agent sẽ không nhanh hơn khi dùng Go.** Một lượt agent là: lắp ráp một prompt, gọi
mô hình (vài giây), chạy một công cụ (I/O mạng hoặc đĩa), và lặp lại. Chi phí trình thông dịch Python
là không đáng kể so với các lời gọi mô hình kéo dài nhiều giây. Việc viết lại vòng lặp tối ưu hóa
một phần vốn không phải là nút thắt cổ chai; tăng tốc mà người dùng cảm nhận được xấp xỉ bằng không.

Nơi Go **thực sự** thắng là có thật, nhưng tất cả đều thuộc về triển khai và điều phối, chứ không phải
trí tuệ: đồng thời I/O ở quy mô lớn (ghép kênh nhiều cổng nhắn tin, các tác vụ cron,
và các người thuê song song), phân phối dưới dạng một tệp nhị phân tĩnh (dễ vận chuyển đến một
máy chủ doanh nghiệp hơn nhiều so với một môi trường Python), và bộ nhớ thấp cùng khởi động nhanh. Đó là các thuộc tính
của control plane và cổng (gateway) — chính là nơi một lớp Go thuộc về.

**Một fork từ bỏ thượng nguồn (upstream) và phá vỡ hệ sinh thái plugin.** Hermes phát hành tính năng
liên tục; một fork Go tại một thời điểm cố định đóng băng ở đó và mãi mãi đuổi theo một mục tiêu di động, khi
các mô hình, cổng, sandbox, và bản vá bảo mật mới xuất hiện trong thượng nguồn (upstream) Python. Hơn nữa,
các skill của Hermes là `SKILL.md` cùng với `plugin.py` với các hook Python, và toàn bộ
hệ sinh thái `agentskills.io` được xây dựng trên nền này. Một runtime Go không thể chạy các plugin đó nếu không
nhúng một trình thông dịch Python (điều này triệt tiêu lợi ích của một tệp nhị phân) hoặc định nghĩa một
plugin ABI mới (mà hệ sinh thái không nhắm tới) — nên một fork sẽ thừa hưởng danh tiếng tên tuổi của Hermes
trong khi đánh mất hệ sinh thái của nó.

**Chi phí cơ hội mới là cái giá thực sự.** Cái giá thực của cuộc viết lại không phải là mã nguồn; mà là
engine kiểm định không được xây dựng trong những tháng đó, trong khi các đối thủ có nguồn lực dồi dào tiến lên.

## Tiền lệ OpenClaw-bằng-Go thực sự cho thấy điều gì

Tồn tại vài bản tái hiện thực OpenClaw bằng Go. Giá trị nổi bật của chúng đồng nhất là
**một tệp nhị phân, bộ nhớ thấp, khởi động nhanh, cách ly đa người thuê (multi-tenant), và tính đồng thời
gốc (native)** — không bao giờ là "một agent thông minh hơn". Điều đó xác nhận luận điểm ở trên: Go thắng ở
triển khai và vận hành, chứ không phải ở trí tuệ, và đó chính là những thuộc tính mà một control plane
doanh nghiệp hoặc runtime đám mây cần. Hơn nữa, OpenClaw là một bề mặt đơn giản hơn, ưu tiên cấu hình
so với Hermes (vốn bổ sung một vòng lặp tự cải thiện, nhiều cổng, nhiều sandbox, một mô hình bộ nhớ
phong phú hơn, và các plugin Python), nên việc port Hermes lớn hơn đáng kể về mặt vật chất. Đây hầu hết là
các dự án cộng đồng tối ưu hóa việc triển khai của chính họ, chứ không phải các công ty mà lợi thế phòng thủ (moat)
phụ thuộc vào runtime — một mục tiêu khác.

## PostgreSQL mà không cần viết lại

Việc muốn có PostgreSQL không phải là lý do để viết lại agent. Nó thuộc về nơi nó phát huy giá trị
— toàn bộ đều là mã mới viết bằng Go bất kể ra sao:

| Dữ liệu | Kho lưu trữ | Lý do |
|---|---|---|
| Lịch sử phiên, bộ nhớ, skill theo từng agent | SQLite (Hermes-native) | Đơn nút, cục bộ; đã hoạt động sẵn |
| Huy hiệu kiểm định, bản đồ độ phủ, hết hạn, trôi dạt (drift) | PostgreSQL (dịch vụ Go) | Dữ liệu của lợi thế phòng thủ (moat) — có thể truy vấn, đa người thuê (multi-tenant) |
| Kệ (registry), dòng dõi (lineage), royalty (tiền bản quyền) | PostgreSQL (Go) | Liên tổ chức, có giao dịch |
| Người thuê, RBAC, SSO, thanh toán, hạn mức | PostgreSQL (Go control plane) | Control plane doanh nghiệp |

Hermes giữ SQLite cho những gì SQLite giỏi; PostgreSQL nằm ở các lớp kiểm định,
kệ (registry), và control plane.

## So sánh các phương án

| Phương án | Mô tả | Tăng tốc ở nơi quan trọng | Giữ upstream + plugin | PostgreSQL | Nỗ lực | Phán quyết |
|---|---|---|---|---|---|---|
| A. Viết lại toàn bộ Hermes bằng Go | Tái hiện thực toàn bộ agent | ~không (bị giới hạn bởi độ trễ mô hình) | Không | Có (nhưng mọi thứ xây lại) | Rất cao | Tránh |
| B. Chỉ mở rộng Hermes Python | Thiết kế mã nguồn mở giữ nguyên như hiện trạng | không áp dụng | Có | Chỉ doanh nghiệp | Thấp | Khả thi nhưng hạn chế |
| C. Hybrid: runtime Hermes + lợi thế phòng thủ (moat) và control plane bằng Go/PostgreSQL | Agent Python, Go cho kiểm định/registry/danh tính/royalty/control plane | Có, ở nơi Go giúp ích | Có | Có, ở nơi quan trọng | Trung bình | **Được khuyến nghị** |
| D. Các dịch vụ Go có chọn lọc | Ngoài ra chỉ tái hiện thực các đường nóng đã được đo lường (bộ ghép kênh cổng, router) dưới dạng các dịch vụ Go gọi Hermes | Có, có mục tiêu | Có | Có | Trung bình-cao | Chỉ khi một nút thắt cổ chai thực sự được đo lường |

Khuyến nghị là Phương án C ngay bây giờ, chỉ bổ sung Phương án D một cách phẫu thuật khi một nút thắt cổ chai
thực sự được đo lường.

## Ước lượng thời gian cho một cuộc viết lại toàn bộ

Đây là các khoảng ước lượng, không phải cam kết, giả định một đội nhỏ mạnh có sự hỗ trợ của AI. Một
cuộc port từ Python sang Go là một sự chuyển dịch mô thức (từ định kiểu động sang định kiểu tĩnh, goroutine,
lỗi tường minh, các SDK khác nhau cho từng cổng và sandbox), và cái giá thực sự là kiểm thử tương đương (parity testing), chứ không phải
mã bản nháp đầu tiên.

| Phạm vi | Bao gồm | Ước lượng | Ghi chú |
|---|---|---|---|
| MVP tinh gọn | Vòng lặp agent, bộ nhớ (Postgres), skill-dưới-dạng-dữ liệu, một cổng, các công cụ lõi, một nhà cung cấp; một tệp nhị phân | ~1–2.5 tháng | Bản demo chạy được; không tương thích plugin |
| Tương đương diện rộng | Phần lớn MCP theo cả hai chiều, vài cổng, nhiều sandbox, cron, cổng phê duyệt, vòng lặp tự cải thiện, gia cố (hardening) | ~6–12+ tháng | Và một mục tiêu di động |
| Tương đương toàn bộ thực sự | Mọi thứ Hermes làm hôm nay bao gồm cả hệ sinh thái plugin/skill Python | Bất khả thi | Yêu cầu nhúng Python hoặc một ABI mới |
| Bảo trì liên tục | Theo dõi các tính năng và bảo mật của Hermes thượng nguồn (upstream) | ~1–2 FTE, vĩnh viễn | Cái giá mà ước lượng đầu tiên bỏ sót |

Ngay cả một MVP sáu tuần lạc quan cũng tạo ra một agent tệ hơn phụ thuộc hiện tại — ít cổng hơn,
không plugin, tụt hậu về mô hình — trong khi tiêu tốn chính những tuần cần thiết cho
việc kiểm định.

## Khi nào một cuộc viết lại sẽ được biện minh

Chỉ khi tất cả những điều sau trở thành sự thật, mà không điều nào đúng ở hiện tại: một
nút thắt cổ chai thực sự không liên quan đến mô hình được đo lường trong runtime (và ngay cả khi đó, hãy tái hiện thực chỉ dịch vụ đó, chứ không phải
agent); thượng nguồn (upstream) của Hermes đình trệ hoặc giấy phép của nó thay đổi; lợi thế phòng thủ (moat) về kiểm định đã
được vận chuyển và runtime giờ là ràng buộc tăng trưởng; hoặc việc bảo trì một phụ thuộc Python được vendor (đóng gói kèm)
tỏ ra là một gánh nặng lặp đi lặp lại sau khi hybrid đã được thử. Cho đến lúc đó,
nguyên tắc là: đi thuê runtime, sở hữu lợi thế phòng thủ.
