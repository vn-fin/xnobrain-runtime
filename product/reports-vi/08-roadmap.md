# 08 · Lộ trình sản phẩm — Từ Studio đến Terminal

> Kết nối bản dựng hiện tại (hiện trạng), tầm nhìn (twin và terminal), lợi thế phòng
> thủ (moat, kiểm định) và mô hình kinh doanh (mô hình kinh doanh và cấp phép) thành
> một kế hoạch có trình tự. Các giai đoạn phản chiếu tầm nhìn Nêm (Wedge) → Tích lũy →
> Mở kệ → Terminal, ánh xạ vào 9 engine. Quan điểm/tổng hợp — hãy điều chỉnh theo nguồn
> lực của bạn.

## 0. Nguyên tắc dẫn đường

> **Đừng xây terminal. Hãy xây thứ nhỏ nhất chứng minh được lợi thế phòng thủ (moat) từ
> kiểm định, dưới lớp vỏ một công cụ nội bộ hữu ích — rồi để kệ tự lấp đầy chính nó.**

Tầm nhìn nói điều này một cách rõ ràng ("Bloomberg 1982 = Merrill Lynch + trái phiếu").
Các engine hàng hóa (thương phẩm) (brain/execution/connection/routing) đã **xong hoặc
đi thuê**. Hãy dồn gần như toàn bộ công sức mới ròng vào **engine 05–07 & 09**.

## Giai đoạn (Phase) 0 — Ra mắt sản phẩm OSS trung thực (bây giờ → vài tuần tới)

*Mục tiêu: một studio agent mã nguồn mở sạch sẽ, sẵn sàng ra mắt + một ranh giới doanh
nghiệp hoạt động được. Phần lớn đã xong; hãy khép các khoảng trống.*

- [ ] **Chọn giấy phép lõi OSS** (Apache-2.0 hoặc AGPL-3.0) — yếu tố chặn việc ra mắt
      README.
- [ ] Bổ sung NOTICE / kiểm kê giấy phép bên thứ ba (Hermes MIT + router + các phụ
      thuộc).
- [ ] Điều hòa câu chuyện **OSS chỉ Python vs Python+Go** với mã nguồn và bài giới thiệu
      (mô hình kinh doanh và cấp phép §2).
- [ ] Củng cố hợp đồng phiên bản doanh nghiệp (RBAC/SSO/kiểm toán/hạn mức đã được đặc
      tả).
- **Engine đang chạy:** 01–04, 08 (đều là hàng hóa/đi thuê). **Tiền:** phí hạ
  tầng/doanh nghiệp.
- **Cổng chuyển giai đoạn:** một công ty có thể `docker compose up` sản phẩm OSS và một
  đối tác thí điểm có thể chạy phiên bản doanh nghiệp trên máy chủ của họ.

## Giai đoạn (Phase) 1 — Điểm nêm (Wedge): một ngành dọc, một tổ chức, các twin kiểm định được (khởi đầu thực sự)

*Mục tiêu: chứng minh engine 07 tồn tại. Đây là giai đoạn sống-còn của công ty.*

- [ ] **Chọn ngành dọc + quốc gia cho Giai đoạn (Phase) 1.** Tầm nhìn gợi ý **tài chính
      / Việt Nam** (đánh giá cổ phiếu ngành thép, môi giới → bán lẻ). Hãy quyết định rõ
      ràng.
- [ ] **Thu thập ở Xưởng (Workshop capture)** — mở rộng skills/memory của Hermes để ghi
      lại các cặp (tình huống, quyết định, lý lẽ) với một **phần held-out (giữ lại) được
      niêm phong** (engine 06).
- [ ] **Bộ chấm độ trung thành (fidelity scorer) v0** — kiểm tra mức đồng thuận khách
      quan + kiểm tra sự từ chối trả lời + một bộ chấm lý lẽ *đã khử thiên lệch* → điểm số
      + bản đồ vùng phủ (engine 07). Xem tài liệu về lợi thế phòng thủ từ kiểm định.
- [ ] **Vật phẩm huy hiệu (badge artifact)** — điểm số + vùng phủ + hạn dùng + nguồn gốc
      đã xác minh, lưu như một đối tượng hạng nhất có snapshot hậu thuẫn (tái sử dụng cơ
      chế lưu trữ hiện có).
- [ ] **Danh tính (identity) v0** — xác minh chuyên gia có tên thật; thu thập đồng thuận
      (engine 05).
- [ ] Chạy các twin **nội bộ** tại một tổ chức đối tác thí điểm; đầu ra chạm tới người
      dùng thật.
- **Engine đang chạy:** +06, 07, 05 (nội bộ). **Tiền:** phí hạ tầng từ tổ chức.
- **Cổng chuyển giai đoạn:** *"Đây là con số nói rằng twin này suy nghĩ như nhà phân
  tích của chúng ta, và đây là lúc nó hết hạn."* Twin được dùng hàng tuần; một đối tác
  thí điểm sẵn sàng bảo chứng.

## Giai đoạn (Phase) 2 — Tích lũy: nhiều twin, một tổ chức, kiểm định thực sự có sức nặng

*Mục tiêu: cổng kiểm định trở nên chịu lực vì đầu ra chạm tới khách hàng cuối.*

- [ ] **Hội đồng nội bộ** — kết hợp nhiều twin thành một quan điểm nhà (house view); bản
      đồ bất đồng (mở rộng engine 04).
- [ ] **Theo dõi trôi dạt (drift monitoring)** — nhật ký sử dụng thực tế hạ cờ các huy
      hiệu → tái kiểm định (engine 07 luồng ngược).
- [ ] **Quyền hưởng + đo mức dùng (entitlements + metering)** cho các twin (engine 09 —
      tái sử dụng thanh toán doanh nghiệp).
- [ ] "Kỷ luật atom (kết luận + căn cứ + cờ độ phủ)" — cấu trúc đầu ra thành **kết luận +
      bằng chứng + cờ độ phủ** để lỗi không dồn tích dọc theo một chuỗi.
- **Engine đang chạy:** +09 (đo mức dùng). **Tiền:** theo từng nhà môi giới / từng khách
  hàng cuối, white-label.
- **Cổng chuyển giai đoạn:** kiểm định được tin cậy trong nội bộ; lỗi của twin được kiểm
  soát.

## Giai đoạn (Phase) 3 — Mở kệ: kệ (registry) liên tổ chức + royalty

*Mục tiêu: các twin được cho thuê xuyên tổ chức; thị trường và bánh đà royalty bắt đầu
quay.*

- [ ] **Kệ (registry) / kệ (shelf)** — tìm kiếm theo tay nghề, bản đồ vùng phủ công
      khai, huy hiệu có hạn, giá do chuyên gia đặt.
- [ ] **Danh tính công khai** — huy hiệu được người ngoài tin cậy (engine 05 công khai).
- [ ] **Quyết toán royalty (tiền bản quyền) & lineage** (engine 09 lõi, mới ròng) — ai
      dạy cái gì, dùng ở đâu, nợ bao nhiêu.
- [ ] **Giải quyết các ô đỏ** — hợp đồng sở hữu tay nghề + điều khoản trách nhiệm về độ
      trung thành (kiểm định §5, mô hình kinh doanh và cấp phép §4).
- **Engine đang chạy:** tất cả, 09 là trung tâm. **Tiền:** royalty + phí kiểm định.
- **Cổng chuyển giai đoạn:** một tổ chức bên ngoài thuê một twin mà họ tin cậy *nhờ tấm
  huy hiệu*, và một khoản royalty được quyết toán về cho chuyên gia.

## Giai đoạn (Phase) 4 — Terminal: chuẩn xuyên lĩnh vực, xuyên quốc gia

*Mục tiêu: nguồn gốc + độ trung thành trở thành một ngôn ngữ chung; bạn nắm điểm nghẽn.*

- [ ] Kệ (registry) + lineage ở quy mô lớn; các twin xuyên lĩnh vực, xuyên quốc gia.
- [ ] Phí ghế + royalty đa tầng.
- [ ] Chiến đấu để giành **"chỗ ngồi trên màn hình"** — trận chiến khó nhất, dài nhất.
- **Điều kiện:** mở ra từ một *chuẩn đã giành được*, không phải từ trang giấy trắng.

## Các luồng công việc xuyên suốt (chạy liên tục)

- **R&D kiểm định** — chính là lợi thế phòng thủ (moat); không bao giờ "xong". Khử thiên
  lệch cho các bộ chấm, xác thực với các đánh giá mù của chuyên gia, tinh chỉnh mô hình
  trôi dạt/hạn dùng. Bố trí nhân lực như nghiên cứu lõi.
- **Chiến lược nguồn cung** — nhắm tới chuyên môn sắp nghỉ hưu / sắp rời cuộc (điểm nêm
  của Cloneable) để hóa giải bài toán đồng thuận "bức tường nguồn cung" (bối cảnh startup
  §4).
- **Pháp lý/hợp đồng** — sở hữu tay nghề, trách nhiệm pháp lý, đồng thuận, chống mạo danh
  — làm cổng cho mọi giai đoạn; đừng để chúng tụt lại sau sản phẩm.
- **Giữ nguyên các engine hàng hóa ở dạng đi thuê** — **đừng** cố xây vượt
  OpenRouter/LiteLLM hay fork lõi của Hermes; hãy mở rộng từ `xnobrain`, theo `AGENTS.md`.

## Quy tắc trình tự một dòng

> Chứng minh tấm huy hiệu (Giai đoạn (Phase) 1) → làm cho nó có sức nặng (Giai đoạn
> (Phase) 2) → để nó đi kèm royalty mà lưu chuyển (Giai đoạn (Phase) 3) → biến nó thành
> chuẩn mực (Giai đoạn (Phase) 4). Mọi thứ hàng hóa, hãy đi thuê. Mọi thứ thuộc lợi thế
> phòng thủ (05/06/07/09), hãy tự xây — bắt đầu bằng bộ chấm độ trung thành.
