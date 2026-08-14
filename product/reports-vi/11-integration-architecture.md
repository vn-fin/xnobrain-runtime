# Kiến trúc tích hợp & Kế hoạch triển khai

Phần này ghi lại cách hệ thống hiện tại tích hợp runtime Hermes và bộ định tuyến LLM
(LLM router), các đặc tính hiệu năng của việc tích hợp đó, cách các profile (cách ly theo
từng agent) hoạt động, và kiến trúc mục tiêu được khuyến nghị cho sản phẩm thương mại.

## Tích hợp hiện tại: một tiến trình mở rộng Hermes

Ứng dụng mã nguồn mở không chạy một máy chủ riêng gọi Hermes qua mạng. Nó import chính
ứng dụng web-server của Hermes và đăng ký các tuyến (route) tương thích của mình lên đó,
rồi phục vụ ứng dụng kết hợp trên cổng 8642. Trên thực tế, tiến trình đang chạy là máy chủ
web của Hermes cộng với bề mặt quản trị của XNOBrain, trong một tiến trình Python duy nhất
dùng chung một môi trường ảo. Bộ định tuyến LLM chạy như một tiến trình riêng trên cổng
20128 và được truy cập qua HTTP.

Vì XNOBrain dùng chung tiến trình và môi trường, nó tích hợp với Hermes qua nhiều kênh
đồng thời thay vì một ranh giới sạch sẽ duy nhất.

| Cơ chế nền tảng | Cách hoạt động | Chức năng nào sử dụng |
|---|---|---|
| Tiến trình con CLI | Fork binary `hermes`, đọc stdout | Chat/run của agent (streaming và non-streaming); cài đặt một skill từ nguồn từ xa |
| Truy cập hệ thống tệp trực tiếp | Đọc và ghi các tệp profile | Tạo/liệt kê/lấy/xóa agent; cấu hình (toàn cục và theo từng agent); cài/bật/tắt skill cục bộ; bộ nhớ; không gian làm việc; ảnh chụp (snapshot); registry profile; các gói (bundle) di động |
| Truy cập SQLite trực tiếp | Đọc và ghi `state.db` của từng profile | Liệt kê hội thoại và tin nhắn cùng mức sử dụng; tạo/đổi tên/xóa phiên (với một helper trong tiến trình trước) |
| Import trong tiến trình | Gọi trực tiếp các module Python của Hermes | Ghi sổ phiên (`hermes_state`), phê duyệt (`tools.approval`), Kanban (`hermes_cli.kanban_db`), liệt kê profile (`hermes_cli.profiles`) |
| HTTP tới bộ định tuyến | JSON qua localhost | Tất cả chức năng nhà cung cấp: kết nối, OAuth, kiểm thử, ngắt kết nối, models, mức sử dụng, combo |

Hai nhận xét sau đây. Thứ nhất, chỉ có hai thao tác thực sự gọi tới *agent* của Hermes:
chat và cài skill từ xa; mọi thứ còn lại chỉ là đường ống bao quanh dữ liệu của Hermes.
Thứ hai — và quan trọng — đường dẫn chat sinh (tiến trình) một tiến trình `hermes` mới cho
mỗi lượt, dù runtime Hermes giữ nóng đã được nạp sẵn trong cùng tiến trình. Việc sinh tiến
trình theo mỗi lượt đó là chi phí có thể tránh được chính yếu trong thiết kế hiện tại.

## Các chế độ gọi và hiệu năng của chúng

Có ba cách để điều khiển runtime, theo thứ tự mức độ ghép nối tăng dần.

**Tiến trình con CLI cho mỗi lần gọi** (đường dẫn chat hiện tại). Mỗi lượt fork binary
`hermes`, trả toàn bộ chi phí khởi động trình thông dịch, import module, và khởi tạo
profile/skill/router trước khi bất kỳ công việc mô hình nào bắt đầu. Đo trên máy tham chiếu,
ngay cả một lệnh tầm thường `hermes --version` cũng tốn khoảng 0.26–0.28s; một lượt chat
thực sự vốn còn nạp cấu hình, skill và các máy chủ MCP thì cao hơn đáng kể — vào cỡ 0.5–2s
chi phí cố định cho mỗi lần gọi. Cách này được cách ly tiến trình và bền vững (một cú crash
hay treo được giới hạn bằng cách kill tiến trình), và CLI là hợp đồng công khai ổn định của
Hermes.

**Một máy chủ giữ nóng, tồn tại lâu dài, được gọi qua một API cục bộ.** Hermes đi kèm máy
chủ API tương thích OpenAI (OpenAI-compatible) của riêng nó, chạy agent trong tiến trình và
giữ nóng. Nó phơi bày `POST /v1/chat/completions`, `POST /v1/responses`, và một API run
(`POST /v1/runs` với server-sent events, cùng các endpoint phê duyệt và dừng), bên cạnh các
endpoint models, skills và health. Gọi tới máy chủ này loại bỏ hoàn toàn chi phí trình
thông dịch và import theo mỗi lượt, đồng thời giữ runtime trong tiến trình riêng của nó để
cách ly. Đây là sự cân bằng tốt nhất giữa tốc độ và an toàn.

**Import trực tiếp vòng lặp agent trong tiến trình.** Chi phí thấp nhất, nhưng ghép nối
chặt nhất: một cú crash, treo, rò rỉ bộ nhớ, hoặc một vòng lặp đồng bộ gây nghẽn sẽ đánh
sập tiến trình phục vụ, và việc tích hợp bị ràng buộc vào các API nội bộ thay đổi nhanh.
Cách này chỉ phù hợp cho các thành phần nội bộ rẻ và ổn định (như hệ thống hiện tại đã làm
cho việc ghi sổ phiên), không phù hợp cho vòng lặp agent.

Với một lượt agent dài đơn lẻ, chi phí sinh tiến trình chỉ là một phần nhỏ của thời gian
thực (wall-clock). Với các lần gọi ngắn, tốc độ yêu cầu cao, hoặc nhiều người thuê đồng
thời, nó trở thành một phần lớn, và tiến-trình-con-cho-mỗi-lần-gọi còn tạo ra bão tiến
trình dưới tải. Do đó, việc giữ runtime nóng quan trọng nhất đúng ở nơi mà sản phẩm thương
mại đang hướng tới: quy mô đa người thuê (multi-tenant).

## Cách các profile (cách ly theo từng agent) hoạt động

Mỗi agent được đặt tên là một **profile** của Hermes — một thư mục nằm dưới gốc dữ liệu,
chứa cấu hình, persona, bộ nhớ, skill, cơ sở dữ liệu phiên, và không gian làm việc của riêng
nó. Agent mặc định là profile gốc. Các profile mới được gieo mầm bằng cách sao chép từ
profile gốc và được đăng ký trong một manifest profile.

Đường dẫn chat hiện tại đạt được cách ly theo từng agent bằng cách sinh CLI với thư mục
home của profile được truyền vào dưới dạng biến môi trường và không gian làm việc của
profile làm thư mục làm việc. Điều này mang lại các profile tùy ý, không giới hạn, được tạo
động, mỗi cái được cách ly hoàn toàn, mà không cần cấu hình bổ sung. Mô hình cách ly đó là
lý do khiến thiết kế hiện tại dùng tiến-trình-con-cho-mỗi-lượt thay vì máy chủ API giữ nóng.

Sự đánh đổi này quan trọng đối với kiến trúc mục tiêu. Máy chủ API giữ nóng của Hermes phục
vụ **chỉ profile mặc định** trừ khi bật ghép kênh (multiplex) profile, trong trường hợp đó
các profile phụ có thể truy cập qua một tiền tố URL, và chỉ với tập profile cụ thể mà
gateway được cấu hình để phục vụ. Nói cách khác, mặc định máy chủ giữ nóng không cung cấp
cách ly theo từng agent động, không giới hạn mà mô hình tiến trình con cung cấp miễn phí.
Việc dung hòa hiệu năng runtime-giữ-nóng với cách ly theo từng agent là quyết định thiết kế
trung tâm cho kiến trúc thương mại.

## Kiến trúc mục tiêu được khuyến nghị

Topology được khuyến nghị giữ runtime Hermes nóng phía sau API của nó, giữ bộ định tuyến
như một tiến trình riêng, và giới thiệu một control plane bằng Go sở hữu lợi thế phòng thủ
(moat) và điều phối cả hai qua HTTP.

```
  ┌─ Control plane bằng Go — IP thương mại (PostgreSQL) ───────────────────────────┐
  │   engine kiểm định · registry / kệ · định danh · royalty & lineage              │
  │   người thuê · RBAC · SSO · thanh toán · hạn mức                                 │
  │   gọi qua HTTP:                                                                  │
  │     → Máy chủ API Hermes   /v1/chat/completions · /v1/runs (SSE, phê duyệt, dừng)│
  │                            /v1/models · /v1/skills · các endpoint quản trị       │
  │     → Bộ định tuyến LLM     kết nối nhà cung cấp · models · combo · mức sử dụng   │
  └────────────────────────────────────────────────────────────────────────────────┘
```

Đây là mô hình lai được mô tả trong phần build-vs-rewrite: Go và PostgreSQL nơi lợi thế
phòng thủ (moat) tồn tại, một runtime Python giữ nóng cho agent, và một ranh giới HTTP ổn
định giữa chúng. Nó loại bỏ việc sinh tiến trình con theo mỗi lượt, bảo toàn cách ly tiến
trình, và trao cho control plane một hợp đồng có thể phiên bản hóa thay vì việc phân tích
stdout mong manh hay các import nội bộ.

Ba vấn đề phải được giải quyết để áp dụng nó.

**Profile.** Một runtime mặc định đơn lẻ phục vụ chat của một profile đơn lẻ. Để hỗ trợ
chat theo từng agent qua API, hãy chọn một trong các cách: bật ghép kênh (multiplex) và
đăng ký từng profile agent với gateway (phù hợp cho một tập agent cố định, ở mức khiêm tốn);
chạy một runtime giữ nóng cho mỗi agent hoặc mỗi người thuê, với control plane khởi động và
gom nhóm (pool) các runtime rồi định tuyến tới đúng cái (phù hợp tự nhiên cho đám mây đa
người thuê, và nhất quán với mô hình sandbox được quản lý); hoặc giữ mô hình
tiến-trình-con-với-profile-home cho chat khi cần các profile động, không giới hạn với cái
giá là chi phí theo mỗi lượt. Với sản phẩm thương mại đa người thuê, một runtime giữ nóng
cho mỗi agent hoặc mỗi người thuê đang hoạt động là mặc định được khuyến nghị.

**Bề mặt quản trị.** Phần lớn chức năng quản trị hiện tại (agent, cấu hình, skill, bộ nhớ,
không gian làm việc, ảnh chụp snapshot) được triển khai qua truy cập trực tiếp tệp, cơ sở
dữ liệu, và trong tiến trình, thứ mà một tiến trình Go riêng biệt không thể tái sử dụng.
Control plane bằng Go nên ưu tiên các endpoint HTTP gốc của Hermes ở nơi chúng tồn tại, và
chỉ chạm tới các định dạng trên đĩa ở nơi Hermes không phơi bày endpoint nào — tránh trùng
lặp và ghép nối vào bố cục tệp.

**Xác thực.** Thiết kế trong tiến trình hiện tại né tránh việc xác thực dashboard của Hermes
bằng cách đánh dấu các route của riêng nó là đã-được-xác-thực-trước. Một tiến trình Go riêng
biệt phải xác thực với API của Hermes một cách đúng đắn (cổng nội bộ hoặc API key) và với bộ
định tuyến qua sơ đồ token của nó.

Áp dụng topology này chuyển ranh giới tích hợp từ "mở rộng Hermes trong tiến trình" sang
"một client HTTP riêng biệt của API Hermes". Đó là một sự tái kiến trúc có chủ đích thay vì
một phần bổ sung, và đó là hướng đi đúng cho control plane cấp doanh nghiệp. Nó có thể được
thực hiện tăng dần: dựng lên control plane bằng Go gọi tới API Hermes giữ nóng và bộ định
tuyến, sở hữu kiểm định, registry, và thanh toán trong PostgreSQL, và di chuyển các lời gọi
quản trị từ truy cập trực tiếp tệp và cơ sở dữ liệu sang API HTTP của Hermes theo thời gian.

## Các bước triển khai

1. Bật và kiểm chứng máy chủ API giữ nóng của Hermes làm backend chat, thay thế
   tiến-trình-con-cho-mỗi-lượt cho đường dẫn chạy agent; đo cải thiện độ trễ so với một
   profile tiêu biểu có các skill thực và các máy chủ MCP được nạp.
2. Quyết định chiến lược profile (khuyến nghị: một runtime giữ nóng cho mỗi agent hoặc mỗi
   người thuê đang hoạt động, được điều phối và gom nhóm (pool) bởi control plane).
3. Dựng lên control plane bằng Go như một client HTTP của API Hermes và bộ định tuyến, xác
   thực đúng đắn với cả hai.
4. Triển khai mô hình dữ liệu kiểm định trong PostgreSQL — huy hiệu, bản đồ vùng phủ, hạn
   dùng, và trôi dạt — như năng lực đầu tiên của control plane.
5. Di chuyển các thao tác quản trị từ truy cập trực tiếp tệp và cơ sở dữ liệu sang các
   endpoint HTTP gốc của Hermes ở nơi có sẵn.
6. Thêm registry, lineage, và thanh toán royalty (tiền bản quyền) khi các giai đoạn liên tổ
   chức bắt đầu.

Không bước nào trong số này đòi hỏi triển khai lại runtime Hermes. Con đường tới cả hiệu
năng và lợi thế phòng thủ (moat) thương mại là một API Hermes giữ nóng, bộ định tuyến LLM,
và một control plane bằng Go với PostgreSQL bên trên.
