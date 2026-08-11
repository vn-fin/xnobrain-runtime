# 06 · Lợi thế phòng thủ từ kiểm định — Biến Engine 07 thành hiện thực

> Tầm nhìn gọi tên **Trust / Certification** (Niềm tin / Kiểm định) là thứ duy nhất
> chưa ai làm và là lõi của terminal. Một tầm nhìn chưa phải là lợi thế phòng thủ (moat)
> chừng nào metric chưa tồn tại. File này biến lời hứa thành một đề xuất phương pháp luận
> cụ thể, có thể xây dựng được. Đây là phần phân tích/quan điểm (được đánh dấu rõ ràng),
> đặt nền trên tài liệu tầm nhìn + bức tranh toàn cảnh về eval trong
> tài liệu thị trường và các con số — không phải một tuyên bố từ bên ngoài.

## 1. "Độ trung thành" phải nghĩa là gì (và không được là gì)

Từ tầm nhìn: kiểm định rằng một twin là **bản sao trung thành của một chuyên gia có tên**,
*không phải* rằng chuyên gia đó giỏi. Diễn đạt lại theo cách vận hành:

> **Độ trung thành = độ khớp giữa quyết định của twin và chính quyết định của cùng
> chuyên gia đó, trên các ca mà twin chưa bao giờ được huấn luyện/tinh chỉnh (held-out),
> được chấm điểm bởi một giao thức không phụ thuộc lĩnh vực.**

Hai thuộc tính không thể thương lượng (cả hai đều có trong tầm nhìn):

- **Không phụ thuộc lĩnh vực (domain-agnostic)** — bộ chấm điểm so sánh *twin với chuyên gia*,
  nên không cần biết gì về cổ phiếu ngành thép hay chẩn đoán hình ảnh. Đây chính là điều
  khiến một cỗ máy hoạt động được trong mọi ngành. Hãy bảo vệ thuộc tính này quyết liệt;
  khoảnh khắc mà kiểm định cần đến chuyên môn lĩnh vực, terminal ngừng mở rộng quy mô.
- **Nhận biết vùng phủ (coverage-aware)** — twin phải báo hiệu *"ngoài những gì người thầy
  đã dạy"* thay vì bịa ra. Do đó kiểm định đo **cả** độ trung thành trong vùng phủ **lẫn**
  hành vi tự thoái một cách trung thực.

## 2. Đề xuất phương pháp luận cụ thể (v0)

*(Quan điểm — một thiết kế khởi đầu để thử thách sức chịu đựng, không phải một đặc tả hoàn chỉnh.)*

**Bước 1 — Khơi gợi các ca theo cặp trong Workshop.** Khi chuyên gia dạy
(nháp → sửa → *vì sao*; "sổ nghề" của tầm nhìn), thu lấy các bộ ba
**(ca đầu vào, quyết định của chuyên gia, lý lẽ)**. Chia thành **tập dạy** và một
**tập held-out** được niêm phong mà twin không bao giờ thấy trong quá trình tạo tác.

**Bước 2 — Chấm độ trung thành trên tập held-out.** Với mỗi ca held-out, lấy quyết định
của twin và so với quyết định của chuyên gia. Dùng một **pha trộn**, không phải một con số đơn:

| Lớp | Nó đo gì | Vì sao |
|---|---|---|
| **Độ khớp chính xác/có cấu trúc** | với các quyết định có đầu ra được định nghĩa (nhãn, con số, danh sách xếp hạng, mua/giữ/bán) | khách quan, rẻ, không thể gian lận |
| **Tính nhất quán của lý lẽ** | *lập luận* của twin có khớp với các quy tắc mà chuyên gia đã nêu không? | bắt được trường hợp "đúng đáp án, sai lý do" |
| **Chuyên gia chấm mù (blind) lại** | chuyên gia (hoặc một hội đồng đồng cấp) chấm mù twin so với chính câu trả lời của họ | hiệu chỉnh theo ground-truth cho các nghề mang tính chủ quan |
| **Tính đúng đắn của sự tự thoái** | trên các ca cố tình ngoài vùng phủ, twin có tự thoái không? | lời hứa "tự tuyên bố giới hạn" |

**Bước 3 — Kết hợp thành một huy hiệu có hạn dùng.** Ánh xạ pha trộn thành một điểm số
công bố + một **bản đồ vùng phủ** (nó được kiểm định trên loại ca nào) + một **ngày hết hạn dùng**.
Hạn dùng được đặt theo **tốc độ trôi dạt** đo được của nghề đó (nghề tài chính biến động
nhanh thì hết hạn sớm hơn nghề thủ tục ổn định).

**Bước 4 — Theo dõi trôi dạt trong vận hành.** Nhật ký sử dụng thực tế (từ runtime, engine
03/09) phản hồi ngược lại: khi hành vi trực tiếp của twin lệch khỏi hồ sơ đã kiểm định của nó,
**hạ cờ huy hiệu và kích hoạt tái kiểm định** — luồng ngược của tầm nhìn.

### Về LLM làm giám khảo (một cái bẫy đã biết)

Bức tranh toàn cảnh về eval cho thấy các LLM giám khảo có **thiên lệch vị trí, độ dài,
và tự tâng bốc**. Do đó:
- Chỉ dùng LLM làm giám khảo **duy nhất** cho lớp "tính nhất quán của lý lẽ", và **khử thiên lệch**
  (hoán đổi thứ tự, ẩn danh tính, dùng ensemble giám khảo).
- Neo huy hiệu vào **độ khớp khách quan + đánh giá chấm mù của chuyên gia thật**, không bao giờ
  vào một LLM giám khảo đơn. Một chuẩn kiểm định chỉ là "GPT chấm nó" thì không phải lợi thế
  phòng thủ — nó là trách nhiệm pháp lý. Đây là phần R&D khó nhất trong công ty; hãy bố trí
  nhân sự tương xứng.

## 3. Vì sao điều này có thể phòng thủ được (các hiệu ứng mạng)

Engine kiểm định tích lũy thành những tài sản mà đối thủ không thể sao chép:

1. **Kho ngữ liệu sổ nghề (corpus)** — dữ liệu theo cặp (ca, quyết định, lý lẽ, held-out) cho mỗi
   chuyên gia. Lớn lên cùng mỗi twin; là nguyên liệu thô cho cả việc tạo tác *lẫn* chấm điểm.
2. **Dữ liệu trôi dạt/benchmark** — hồ sơ theo thời gian về cách các nghề suy giảm; không ai khác
   có nó, và nó thiết lập chính sách hạn dùng khiến huy hiệu trở thành doanh thu định kỳ.
3. **Bản thân huy hiệu được tin cậy** — một khi người mua (quỹ, bệnh viện, doanh nghiệp) *tin
   huy hiệu*, chuẩn đó chính là lợi thế phòng thủ (Phase 4). Các chuẩn có tính winner-take-most
   (kẻ thắng chiếm phần lớn).
4. **Lineage của royalty (tiền bản quyền)** (engine 09) — sổ ghi ai đã dạy gì, dùng ở đâu,
   nợ bao nhiêu. Một lớp thanh toán bù trừ có chi phí chuyển đổi.

## 4. Nên xây gì trước (twin kiểm định được tối thiểu)

Bạn **không** cần toàn bộ terminal để chứng minh lợi thế phòng thủ. Lát cắt khả tín nhỏ nhất:

1. **Thu bắt trong Workshop** — mở rộng skills/memory của Hermes để ghi lại các cặp
   (ca, quyết định, lý lẽ) với một phân tách held-out được niêm phong. *(Xây trên engine 02,
   vốn đã tồn tại.)*
2. **Bộ chấm độ trung thành v0** — độ khớp khách quan + kiểm tra tự thoái + một giám khảo lý lẽ
   đã khử thiên lệch; xuất ra một điểm số + bản đồ vùng phủ. *(Mới — engine 07.)*
3. **Một đối tượng huy hiệu** — điểm số, vùng phủ, hạn dùng, xuất xứ (chuyên gia nào, đã xác minh).
   Lưu nó như một tạo tác hạng nhất, được hậu thuẫn bằng snapshot trong một profile. *(Mở rộng
   máy móc snapshot/persistence hiện có.)*
4. **Một khách hàng nội bộ, một mảng dọc** — chạy nó theo kiểu Phase-1 (mũi nêm của tầm nhìn):
   một vài chuyên gia thật, các twin dùng nội bộ, kiểm định có ý nghĩa vì đầu ra chạm tới
   người dùng cuối thật.

Nếu một twin có thể được tạo tác, chấm điểm trên các ca held-out, gắn huy hiệu, và trình cho một
đối tác thiết kế thấy *"đây là con số nói rằng nó tư duy giống nhà phân tích của chúng ta, và đây
là thời điểm nó hết hạn,"* thì luận điểm cốt lõi đã được chứng minh — mọi thứ còn lại (registry,
royalty, các hội đồng liên tổ chức) chỉ là mở rộng quy mô.

## 5. Các câu hỏi mở cần giải quyết trước khi mở rộng quy mô (từ những ô đỏ của tầm nhìn)

- **Quyền sở hữu nghề** — chuyên gia và người sử dụng lao động; ai được phép xuất một twin;
  việc thu hồi. Soạn thảo các điều khoản hợp đồng; tầm nhìn đánh dấu điều này là chưa được giải
  và nó chặn cái kệ (shelf) (Phase 3).
- **Trách nhiệm pháp lý** — kiểm định bảo chứng cho *độ trung thành*, dứt khoát **không phải**
  tính đúng đắn hay sự phù hợp. Điều này phải chặt chẽ về mặt hợp đồng, nếu không lời khuyên của
  một twin trung thành-nhưng-tồi sẽ trở thành vấn đề pháp lý của bạn. Giấy phép/sơ suất nghề nghiệp
  thuộc về tổ chức khách hàng.
- **Chống mạo danh / sự đồng thuận** — danh tính người thật đã được xác minh (engine 05) và
  sự đồng thuận rõ ràng là những điều kiện tiên quyết, không phải phần thêm vào; một bản sao trung
  thành của người không đồng thuận là một vụ kiện.
