# Tóm tắt điều hành

Brain4All là một không gian làm việc tự lưu trữ (self-host) để xây dựng và vận hành AI agent, hiện được xây dựng trên **Hermes Agent** mã nguồn mở (MIT) của Nous Research cùng một bộ định tuyến LLM đa nhà cung cấp, với một mô hình phân tách open-core được thiết kế sẵn (một runtime công khai và một control plane doanh nghiệp riêng tư). Các năng lực đã trở thành hàng hóa phổ biến — vòng lặp agent, bộ nhớ, skill, tích hợp công cụ/MCP, và định tuyến nhà cung cấp — đều đã được xây dựng hoặc đi thuê. Tài liệu này trình bày sản phẩm là gì, thị trường mà nó bước vào, bối cảnh cạnh tranh, lợi thế phòng thủ (moat) có thể bảo vệ được, mô hình kinh doanh, kiến trúc kỹ thuật, và một kế hoạch theo từng giai đoạn để xây dựng sản phẩm thương mại.

## Luận điểm sản phẩm

Mục tiêu thương mại là một **"Bloomberg Terminal cho các twin AI chuyên gia đã được kiểm định."** Các chuyên gia trong lĩnh vực mã hóa tay nghề của họ thành các **twin** skill/quy trình. Mỗi twin phải vượt qua một **cổng kiểm định độ trung thành** — một phép đo xác nhận rằng twin tái tạo trung thực phán đoán của một chuyên gia *thật, có tên tuổi* trên các trường hợp giữ lại (held-out) — giành được một **huy hiệu tin cậy có hạn dùng**, được niêm yết trong một **kệ (registry)**, chạy **trên chính dữ liệu và runtime của khách hàng**, và trả **royalty (tiền bản quyền)** lại cho chuyên gia. Việc giám sát trôi dạt buộc phải tái kiểm định theo thời gian.

Cái nhìn chiến lược nằm ở phạm vi áp dụng. **Đo lường độ trung thành là phi phụ thuộc lĩnh vực** — nó so sánh các quyết định của một twin với quyết định của chính chuyên gia đó, không cần bất kỳ hiểu biết nào về chuyên ngành — nên một cỗ máy duy nhất hoạt động được cho một chuyên viên phân tích thép, một bác sĩ chẩn đoán hình ảnh, hay một luật sư M&A. **Đánh giá xem bản thân chuyên gia có giỏi hay không thì lại phụ thuộc lĩnh vực**, nên terminal cố tình từ chối công việc đó và để thị trường quyết định. Chính sự phân biệt này khiến nền tảng vừa có khả năng phòng thủ vừa mở rộng được theo chiều ngang.

## Thị trường lớn, tăng trưởng nhanh, và đang được xác thực

- Thị trường AI agentic vào khoảng **9–12 tỷ USD năm 2026, tăng trưởng 35–50% mỗi năm** theo nhiều dự báo độc lập khác nhau.
- Mọi nền tảng lớn đều đã ra mắt một chợ (marketplace) agent — chợ của Salesforce đã đạt **18.500 khách hàng trong chưa đầy một năm** — nhưng các chợ thiếu kiểm định sẽ biến sản phẩm thành hàng hóa phổ thông (kinh tế học của GPT Store quy ra khoảng **0,03 USD mỗi cuộc hội thoại**).
- Việc nhân bản chuyên gia đang được rót vốn nhanh và mạnh: **Viven** huy động **35 triệu USD vòng seed** cho "twin số của nhân viên" phục vụ doanh nghiệp, **Delphi** huy động **16 triệu USD** từ Sequoia, và **Cloneable** báo cáo **tăng trưởng ARR gấp 100 lần**. Quy mô các vòng gọi vốn digital-twin đã tăng khoảng **gấp đôi** so với năm trước.
- Việc áp dụng đánh giá AI (AI-evaluation) được dự báo sẽ **tăng gấp ba, lên 60% số đội ngũ kỹ thuật vào năm 2028**.

## Khoảng trống thị trường — và nó đang thu hẹp lại

Ô "nhân bản một chuyên gia có tên tuổi" hiện đã đông đúc: Viven, IgniteTech, Interloom, Cloneable, Delphi, và Coachvox đều nhân bản con người. Vì vậy "Chúng tôi tạo ra twin chuyên gia" đã **không còn là điểm khác biệt**. Điều mà *không ai* làm — được kiểm chứng qua hơn hai mươi công ty — là **kiểm định rằng một bản nhân bản tái tạo trung thực một chuyên gia cụ thể có tên tuổi** (được đo lường, kèm một huy hiệu có hạn dùng), **giám sát trôi dạt**, và **quyết toán royalty** xuyên các tổ chức trong khi twin chạy trên **chính runtime của khách hàng**. Các startup đánh giá đo *chất lượng ứng dụng*, chứ không phải độ trung thành so với một con người; các chợ thẩm định *đối tác*, chứ không thẩm định con người. Mệnh lệnh chiến lược là dẫn dắt bằng **kiểm định và royalty**, chứ không phải bằng "twin" — nếu không, Brain4All chỉ là một trong hàng chục cái tên.

## Lợi thế phòng thủ (moat), được cụ thể hóa

Độ trung thành được định nghĩa về mặt vận hành là mức độ đồng thuận giữa quyết định của một twin và quyết định của chính chuyên gia đó trên **các trường hợp giữ lại mà twin chưa từng thấy**, được chấm bằng một **tổ hợp** — mức đồng thuận khách quan, tính nhất quán của lập luận, việc chuyên gia chấm lại một cách ẩn danh (blind), và việc từ chối trả lời đúng đắn trên các trường hợp ngoài vùng phủ — **không bao giờ dựa vào một LLM-làm-giám khảo (LLM-as-judge) đơn lẻ** (mà các thiên lệch của nó đã được ghi nhận). Cổng kiểm định này tích lũy thành những tài sản mà đối thủ không thể sao chép: kho ngữ liệu sổ nghề được ghép cặp, các benchmark trôi dạt theo chiều dọc thời gian biện minh cho hạn dùng của huy hiệu, bản thân chuẩn huy hiệu đáng tin cậy, và sổ quyết toán royalty theo lineage.

## Mô hình kinh doanh và công nghệ

Công ty đi theo một mô hình **open-core** đã được kiểm chứng: một runtime mã nguồn mở kiểu permissive/AGPL cộng với một control plane doanh nghiệp đóng. Vì lợi thế phòng thủ (kiểm định, kệ registry, danh tính, royalty, thanh toán) là riêng tư theo thiết kế, phần lõi có thể giữ được sự mở thực sự, tối đa hóa mức độ áp dụng. Các doanh nghiệp tự lưu trữ (self-host) hoặc chạy trên một đám mây được quản lý; doanh thu tiến hóa từ giấy phép doanh nghiệp sang phí theo từng twin rồi tới phí kiểm định và royalty.

Về mặt kỹ thuật, kiến trúc được khuyến nghị giữ cho runtime của Hermes agent luôn "nóng" (warm) phía sau API riêng của nó, định tuyến lưu lượng LLM qua bộ định tuyến, và xây dựng lợi thế phòng thủ cùng control plane trong một lớp **Go + PostgreSQL** riêng biệt. Việc tái triển khai toàn bộ runtime của agent được nêu rõ là *không* được khuyến nghị (xem các mục kiến trúc và xây-mới-hay-viết-lại).

## Kế hoạch gói gọn trong một dòng

Phát hành sản phẩm mã nguồn mở với một giấy phép rõ ràng; chứng minh lợi thế phòng thủ kiểm định bằng một điểm nêm (wedge) nội bộ ở Giai đoạn 1 trong một ngành dọc duy nhất; sau đó làm cho kiểm định trở nên quan trọng trên phạm vi toàn tổ chức, mở một kệ xuyên tổ chức kèm royalty, và cuối cùng thiết lập chuẩn xuất xứ-và-độ-trung-thành thành ngôn ngữ chung của toàn ngành.

## Những điểm làm rõ then chốt xuyên suốt báo cáo này

- **Hermes là một sản phẩm thật** — chính là Hermes Agent do Nous Research phát hành theo giấy phép MIT — không phải một bí danh nội bộ. MIT cho phép xây dựng một lớp thương mại đóng lên trên, nhưng điều đó cũng có nghĩa là lợi thế phòng thủ không thể là "chúng tôi bọc quanh Hermes"; nó phải là kiểm định, dữ liệu nghề, danh tính, và royalty.
- **Runtime mã nguồn mở dùng Python; Go thuộc về lớp doanh nghiệp.** Một hướng đi Go/Postgres trước đây trong kho mã nguồn mở đã bị loại bỏ; Go giờ đây nằm ở phía control plane doanh nghiệp.
- **Phần lõi mã nguồn mở vẫn cần chọn một giấy phép** trước khi phân phối công khai — một điều kiện tiên quyết cứng cho việc ra mắt.
- Các con số ước lượng quy mô thị trường mang tính định hướng (các hãng độc lập chênh nhau khoảng ~25%); các khoảng giá trị được đưa ra thay vì một điểm ước lượng đơn lẻ.
