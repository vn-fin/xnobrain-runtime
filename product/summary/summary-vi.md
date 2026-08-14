# XNOBrain — Twin Terminal · Tóm tắt điều hành

## Một câu

XNOBrain là nền tảng tự lưu trữ (self-host) để xây dựng AI agent, đang tiến hóa thành một
**"Bloomberg Terminal cho các twin chuyên gia đã kiểm định"**: chuyên gia mã hóa tay nghề
của mình vào các twin, các twin này đi qua cổng kiểm định độ trung thành, chạy trên dữ liệu
của chính khách hàng, và trả royalty (tiền bản quyền) ngược lại cho chuyên gia.

## Sản phẩm

- **Hiện tại** — một không gian làm việc agent tự lưu trữ, xây trên **Hermes Agent** (MIT)
  của Nous Research cùng một bộ định tuyến LLM đa nhà cung cấp, với mô hình open-core (một
  runtime công khai và một control plane doanh nghiệp đóng).
- **Mục tiêu** — chuyên gia mã hóa tay nghề vào các **twin** dạng skill/quy trình. Mỗi twin
  đi qua **cổng kiểm định độ trung thành** (bằng chứng nó tái hiện trung thành phán đoán của
  một chuyên gia *có tên thật* trên các ca held-out), nhận **huy hiệu có hạn dùng**, được
  niêm yết trên **kệ (registry)**, chạy trên dữ liệu và runtime của chính khách hàng, và trả
  **royalty** cho chuyên gia. Việc theo dõi trôi dạt buộc phải tái kiểm định.
- **Điểm cốt lõi về phạm vi** — đo *độ trung thành* là việc độc lập với ngành (một cỗ máy
  dùng cho mọi ngành); còn đánh giá chuyên gia đó *có giỏi hay không* thì không — nên
  terminal chủ động từ chối việc đó.

## Lợi thế phòng thủ (moat)

Độ trung thành là mức độ khớp giữa phán đoán của twin và phán đoán của chính chuyên gia trên
các **ca held-out (giữ lại để kiểm)**, được chấm bằng một tổ hợp — độ khớp khách quan, sự
nhất quán về lý lẽ, chuyên gia chấm mù (blind) lại, và sự tự thoái đúng lúc — **không bao giờ
chỉ dựa vào một LLM làm giám khảo**. Cổng kiểm định tích tụ thành các tài sản không thể sao
chép: kho sổ nghề, dữ liệu chuẩn về trôi dạt, chuẩn huy hiệu được tin cậy, và sổ cái
royalty–lineage.

## Thị trường

- Thị trường AI tác tử (agentic AI) **khoảng 9–12 tỷ USD năm 2026, tăng 35–50%/năm**.
- Mọi nền tảng đều đã ra mắt chợ agent (chợ của Salesforce đạt **18.500 khách hàng trong
  chưa đầy một năm**), nhưng chợ không có kiểm chứng sẽ bị thương phẩm hóa (GPT Store ≈
  **0,03 USD mỗi cuộc hội thoại**).
- Nhân bản chuyên gia được rót vốn nhanh: **Viven gọi 35 triệu USD vòng seed** (twin nhân
  viên doanh nghiệp), **Delphi 16 triệu USD / Sequoia**, **Cloneable tăng trưởng ARR 100
  lần**; quy mô vòng gọi vốn của mảng digital twin gần như **gấp đôi** so với năm trước.
- Mức độ áp dụng công cụ đánh giá AI dự kiến **tăng gấp ba, lên 60% đội ngũ kỹ thuật vào năm
  2028**.

## Khoảng trống thị trường (và đang thu hẹp)

Ô "nhân bản một chuyên gia có tên thật" nay đã chật chội — Viven, IgniteTech, Interloom,
Delphi, Coachvox đều đang nhân bản con người. **Chưa ai kiểm định độ trung thành so với một
chuyên gia có tên thật cụ thể** kèm huy hiệu có hạn dùng, theo dõi trôi dạt, và thanh toán
royalty xuyên tổ chức trong khi twin chạy trên runtime của chính khách hàng. Mệnh lệnh chiến
lược: **dẫn dắt bằng kiểm định và royalty, chứ không phải bằng "twin".**

## Mô hình kinh doanh & công nghệ

- **Open-core** — một runtime mã nguồn mở (permissive/AGPL) cộng một control plane doanh
  nghiệp đóng. Vì lợi thế phòng thủ vốn đã ở phần đóng theo thiết kế, nên phần lõi có thể mở
  thật sự.
- **Không viết lại Hermes bằng Go.** Vòng lặp agent bị giới hạn bởi độ trễ mô hình, nên việc
  viết lại không tăng tốc thật sự, đồng thời đánh mất dòng cập nhật thượng nguồn của Hermes
  và phá vỡ hệ sinh thái plugin Python. Hãy giữ Hermes "nóng" phía sau API của nó, định tuyến
  qua bộ định tuyến LLM, và xây lợi thế phòng thủ cùng control plane bằng **Go + PostgreSQL**.

## Kế hoạch

0. **Ra mắt sản phẩm mã nguồn mở** — chọn giấy phép (một điều kiện tiên quyết để ra mắt).
1. **Nêm (Wedge)** — một ngành dọc: xây phần thu thập ở Xưởng, một bộ chấm độ trung thành, và
   huy hiệu; chạy twin nội bộ tại một đối tác thí điểm. Đây là bước chứng minh lợi thế phòng
   thủ.
2. **Tích lũy** — hội đồng nội bộ, theo dõi trôi dạt, đo mức dùng.
3. **Mở kệ** — một kệ (registry) xuyên tổ chức kèm royalty.
4. **Terminal** — xuất xứ và độ trung thành trở thành chuẩn chung của ngành.

## Những đính chính quan trọng

- **Hermes là một sản phẩm có thật** (Nous Research, MIT) — không phải bí danh nội bộ.
- **Runtime mã nguồn mở dùng Python**; Go thuộc về lớp doanh nghiệp.
- **Phần lõi mã nguồn mở vẫn cần chọn một giấy phép** trước khi phân phối công khai.
