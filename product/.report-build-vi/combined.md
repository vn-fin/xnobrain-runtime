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


# Tầm nhìn — Giải phẫu "Twin Terminal"

> Phạm vi: bản chuyển ngữ và phân tích trung thành sang tiếng Anh của hai tài liệu
> tầm nhìn tiếng Việt trong `product/` (`twin_terminal_product_map.html` và
> `twin_terminal_flow_network.html`). Không có gì ở đây được bịa ra — đây là tầm nhìn
> sản phẩm được trình bày lại cho một đối tượng làm việc thực tế, và được kiểm thử
> đối chiếu với bản dựng hiện tại cùng thị trường.

## 1. Luận điểm một dòng

> **"Bloomberg bảo chứng cho dữ liệu. Terminal này bảo chứng cho twin."**

Sản phẩm của Bloomberg chưa bao giờ là "dữ liệu tài chính" một cách trừu tượng — nó là
một đường ray **đáng tin, chuẩn hóa, đã được xác minh** mà mọi chuyên gia ngồi trước
màn hình suốt cả ngày. Twin Terminal đề xuất chính nước cờ đó cho **các twin AI chuyên
gia**: mỗi twin mã hóa nghề (kỹ năng/quy trình làm việc) của một chuyên gia có tên tuổi,
và nhiệm vụ của terminal là **bảo chứng rằng twin là một bản sao trung thành của con
người thật, có tên đó** — chứ không phải phán xét xem chuyên gia đó có giỏi hay không.

Chính sự phân biệt đó là toàn bộ chiến lược, và đó là lý do cỗ máy này
**không phụ thuộc ngành nghề**:

- **Đo độ trung thành (fidelity)** = so sánh đầu ra của twin với chính phán đoán của
  *cùng một chuyên gia* trên các ca held-out (giữ lại để kiểm). Việc này **không cần
  kiến thức chuyên ngành** → một cỗ máy chạy được cho chuyên viên phân tích ngành thép,
  bác sĩ chẩn đoán hình ảnh, hay luật sư M&A.
- **Phán xét chất lượng chuyên môn** = cần kiến thức chuyên ngành sâu → terminal
  **cố ý từ chối** công việc này và để lại cho thị trường.

Phép loại suy Bloomberg, nêu trong tài liệu: *Bloomberg bảo chứng rằng giá được báo
cáo chính xác; nó không bảo chứng rằng trái phiếu đó đáng mua.* Cùng một logic.

## 2. Năm lời hứa (bản giao kèo của cổng kiểm định)

Mọi twin đi qua terminal đều được bảo chứng là:

1. **Xuất xứ thật** — thực sự đến từ con người có tên; danh tính đã xác minh,
   chống mạo danh.
2. **Có số đo** — độ trung thành so với chuyên gia, được chấm điểm trên các ca held-out.
   Huy hiệu **có hạn dùng (expire)**.
3. **Cài một chạm** — không cần dự án tích hợp; cắm thẳng vào dữ liệu sẵn có của khách.
4. **Chạy ổn định** — không sập giữa chừng, không lặp vô tận, chi phí dự đoán được.
5. **Tự khai** — khi ra ngoài vùng phủ, nó nói *"thầy chưa bao giờ dạy tôi điều này"* —
   nó không bịa.

Điều terminal **bảo lãnh**: bản sao trung thành, xuất xứ thật có tên, cài được và ổn
định. Điều terminal **minh thị không bảo lãnh**: chuyên gia có giỏi hay không, hoặc lời
khuyên có đúng hay không. *Thị trường phán xét con người; terminal phán xét bản sao.*

## 3. Dòng chảy (chiếc đồng hồ cát)

Cả hai tài liệu đều mô tả một **chiếc đồng hồ cát nhiều-tới-nhiều với một điểm nghẽn
duy nhất** — thắng được điểm nghẽn đó là toàn bộ chiến lược.

```
NGUỒN CUNG (chuyên gia, mọi lĩnh vực, mọi quốc gia)
   → Xưởng dạy nghề (dạy nghề: bản nháp–chỉnh sửa–vì sao, các ca đối chiếu, đoán-luật-để-bác-bỏ → "sổ nghề", MIỄN PHÍ)
      → ★ CỔNG KIỂM ĐỊNH  (xuất xứ người thật · độ trung thành · độ ổn định — TRƯỢT = không lên kệ)  ← lõi
         → Kệ / Registry (tìm theo nghề · huy hiệu + hạn dùng · bản đồ vùng phủ công khai · chuyên gia đặt giá)
            → Runtime + Hội đồng (cài một chạm · chạy trên dữ liệu khách · bảng twin liên tổ chức)
               → Lớp thương mại (quyền dùng · đo đếm · lineage & royalty)
NHU CẦU (khách hàng, mọi lĩnh vực, mọi quốc gia) — trả: phí chỗ ngồi + phí trên mỗi twin đã cài
```

Ba **dòng chảy ngược** ở đáy giữ cho hệ thống sống:

- **Log sử dụng** → đo độ trôi dạt → kích hoạt **tái kiểm định** (nghề bị suy giảm theo
  thời gian, ví dụ nghề tài chính).
- **Royalty (tiền bản quyền)** → chảy ngược về chuyên gia và tổ chức chủ quản của họ.
- **Các ca khó** → phản hồi trở lại vòng dạy nghề.

Hai **câu hỏi hộp đỏ chưa có lời giải** mà tài liệu thẳng thắn nêu lên:

- **Phía cung:** *Ai sở hữu nghề?* (chuyên gia, hay chủ sử dụng lao động của họ? ai được
  bấm nút "export"?) — phải được chốt bằng hợp đồng ngay từ đầu.
- **Phía cầu:** *Giấy phép/trách nhiệm pháp lý nằm ở đâu?* — ở tổ chức khách hàng, không
  phải ở terminal. Rủi ro pháp lý từ cơ quan quản lý + sơ suất nghề nghiệp thuộc về bên
  mua.

## 4. Chín engine (và engine nào là moat)

Chú giải màu từ tài liệu: **xám = đi thuê / chuẩn mở**, **xanh mòng két (teal) = tài sản
tích lũy**, **đồng thau (brass) = chính terminal, phải tự xây**.

| # | Engine | Loại | Ghi chú |
|---|---|---|---|
| 01 | **Brain** (LLM đa mô hình, thay thế được) | xám | *Không bao giờ là moat.* |
| 02 | **Memory** (sổ nghề đi theo twin) | teal | Cơ chế là hàng phổ thông; **nội dung mới là tài sản.** |
| 03 | **Execution** (chạy twin trên dữ liệu khách, sandbox, chi phí dự đoán được) | xám | |
| 04 | **Orchestration** (bộ định tuyến theo nghề, hội đồng liên tổ chức, bản đồ bất đồng) | xám→ | |
| 05 | **Identity** (xác minh người thật, chống mạo danh, gắn tên lên từng đơn vị) | **đồng thau** | Moat. |
| 06 | **Improvement loop** (trích xuất tri thức ngầm — thứ lấp đầy kệ) | **đồng thau** | Moat. *"Chưa nền tảng nào có cái này."* |
| 07 | **Trust / Certification (Tin cậy / Kiểm định)** ★ | **LÕI đồng thau** | Điều không ai làm: độ trung thành + cờ độ phủ + trôi dạt + **huy hiệu có hạn dùng.** |
| 08 | **Connection** (MCP/connector, chat/nhúng/API) | xám | Cưỡi lên chuẩn mở. |
| 09 | **Commerce** (quyền dùng, đo đếm, thanh toán, **lineage & royalty**) | **đồng thau** | Moat. |

**Ánh xạ vào bản dựng hiện tại** (xem báo cáo hiện trạng): Engine 01, 03, 08 và phần lớn
04 **đã hoàn thiện hoặc đi thuê** qua Hermes + 9router. *Cơ chế* của Engine 02 đã tồn tại
(memory/skills của Hermes). Các Engine **05, 06, 07, 09** là những moat greenfield — và
07 là hạng mục khó nhất, đòn bẩy cao nhất phải xây.

## 5. Trình tự go-to-market (terminal lấp đầy như thế nào)

Tài liệu thẳng thắn một cách sảng khoái rằng **terminal là đích đến, không phải điểm
xuất phát** — *"Bloomberg 1982 = Merrill Lynch + trái phiếu."* Trình tự:

| Giai đoạn | Tên | Hình hài | Engine bật | Tiền | Cổng để tiến lên |
|---|---|---|---|---|---|
| **1** | Nêm (Wedge) | Một lĩnh vực, một quốc gia. Vài chuyên gia; twin dùng **nội bộ** trong một tổ chức. Chưa có kệ. | 01–04, 06 | Phí hạ tầng từ tổ chức | Twin thực sự hội tụ; dùng hằng tuần |
| **2** | Tích lũy (Accumulate) | Nhiều twin, một tổ chức. Hội đồng nội bộ, quan điểm nhà (house view). **Kiểm định lần đầu trở nên có ý nghĩa** (đầu ra chạm tới khách hàng cuối). | +07, 09 | Theo môi giới & khách hàng cuối, white-label | "Kỷ luật đơn vị" — lỗi không nhân dồn xuống dây chuyền |
| **3** | Mở kệ (Open the shelf) | Liên tổ chức, một lĩnh vực. Twin cho các quỹ, ngân hàng quản lý tài sản, tổ chức khác thuê. **Royalty bắt đầu chảy.** | 05 công khai, 09 làm trung tâm | Royalty + phí kiểm định | Huy hiệu được người ngoài tin |
| **4** | Terminal | Liên lĩnh vực, liên quốc gia. Chuẩn xuất xứ + độ trung thành trở thành **ngôn ngữ chung.** | tất cả + registry + lineage | Phí chỗ ngồi + royalty đa tầng | Mở ra từ một chuẩn đã giành được, không phải từ trang giấy trắng |

Bài toán con-gà-quả-trứng **tự tan biến** vì nguồn cung cho kệ được tạo ra trong Giai
đoạn 1–2 dưới vỏ bọc của một công cụ nội bộ.

**Hai bài toán khó, chưa được giải mà tài liệu nêu thẳng thắn:**

1. **Bức tường nguồn cung** — liệu những chuyên gia thực sự xuất sắc có đồng ý bị nhân
   bản không?
2. **Chỗ ngồi trên màn hình** — bất động sản đắt nhất thế giới; Bloomberg mất khoảng 40
   năm để giành được.

## 6. Vì sao tầm nhìn này có tính phòng thủ (góc nhìn của nhà phân tích)

- Nó chọn một **moat không phụ thuộc ngành nghề** (đo độ trung thành) trong khi **từ
  chối** những phần đòi hỏi chuyên môn ngành — một lựa chọn khoanh vùng hiếm gặp và có
  kỷ luật.
- Nó có một **con đường khởi động nguội đáng tin** (công cụ nội bộ → kệ) thay vì kế hoạch
  xây-chợ-rồi-cầu-nguyện.
- Nó gắn kèm một **cơ chế doanh thu định kỳ và có tính phòng thủ** (huy hiệu có hạn dùng
  + trôi dạt → buộc tái kiểm định; royalty + lineage) thay vì một lần bán đứt.
- Các moat cộng dồn: danh tính + kiểm định + sổ nghề + lineage royalty là những **tài
  sản dữ liệu/mạng lưới mạnh lên theo mức sử dụng** và khó sao chép.

## 7. Nơi tầm nhìn cần được mài sắc (đầu vào cho lộ trình)

1. **Định nghĩa thước đo độ trung thành.** "Nhất quán với chuyên gia trên các ca
   held-out" là tuyên bố lõi — nó cần một phương pháp luận cụ thể, có tính phòng thủ
   (thước đo, giao thức held-out, ánh xạ điểm→huy hiệu, chính sách hạn dùng). Đây là tài
   liệu quan trọng nhất cần soạn tiếp theo. Xem báo cáo về moat kiểm định.
2. **Hợp đồng sở hữu nghề** (hộp đỏ phía cung) — điều khoản mẫu cho IP giữa chuyên
   gia-và-chủ sử dụng lao động, quyền export, và thu hồi.
3. **Ranh giới trách nhiệm pháp lý** (hộp đỏ phía cầu) — ngôn ngữ hợp đồng minh thị rằng
   giấy phép/sơ suất nghề nghiệp thuộc về tổ chức khách hàng.
4. **Twin ≠ chuyên gia, cả về pháp lý lẫn UX** — chống mạo danh và nhãn dán rõ ràng "bản
   sao trung thành, không phải con người" để tránh rủi ro lừa dối và phỉ báng.
5. **Ngành dọc nào là Giai đoạn 1?** Sơ đồ dòng chảy gợi ý **tài chính tại Việt Nam**
   (phán đoán cổ phiếu ngành thép, công ty chứng khoán → nhà đầu tư cá nhân). Đó nên là
   một quyết định minh thị, vì mọi thứ ở hạ nguồn đều phụ thuộc vào nó.


# Thị trường & Số liệu

> Những con số cụ thể kèm nguồn và ngày tháng, để hỗ trợ tầm nhìn và câu chuyện
> gọi vốn. Nơi các công ty nghiên cứu bất đồng, tất cả ước tính đều được trình bày —
> đừng chọn lọc một con số duy nhất mà không nêu khoảng. Các báo cáo định cỡ thị trường
> mang tính định hướng, nên nêu khoảng thay vì một điểm; hãy xem chúng như con số ước
> lượng theo bậc độ lớn.

## 1. Thị trường AI tác tử (agentic AI) (danh mục của bạn)

Các ước tính về quy mô thị trường năm 2026 tập trung quanh mức **$9–12 tỷ**, với dự báo
tăng trưởng rất cao (CAGR khoảng giữa 30% đến ~50%):

| Nguồn | Quy mô 2026 | CAGR | Dài hạn |
|---|---|---|---|
| [Fortune Business Insights](https://www.fortunebusinessinsights.com/agentic-ai-market-114233) | ~$9.14B | 40.5% (→2034) | ~$139B vào 2034 |
| [Grand View Research](https://www.grandviewresearch.com/industry-analysis/ai-agents-market-report) | ~$10.9B | 49.6% (2026–33) | ~$182.9B vào 2033 |
| [Precedence Research](https://www.precedenceresearch.com/ai-agents-market) | ~$11.55B | 43.57% (2026–35) | ~$294.66B vào 2035 |
| [Roots Analysis](https://www.rootsanalysis.com/ai-agents-market) | — | 34.64% (→2035) | — |

**Đánh giá trung thực:** *mức tuyệt đối* thì còn bất định (phương pháp luận chênh nhau
~25%), nhưng *xu hướng* — một thị trường vài chục tỷ đô đang tăng 35–50%/năm — thì nhất
quán giữa các công ty độc lập. Đủ tốt để neo cho câu chuyện "lớn và nhanh"; đừng viện
dẫn quá mức một con số ước tính điểm duy nhất.

## 2. Nền kinh tế agent / chợ agent (hình hài Giai đoạn 3–4 của bạn)

Mọi nền tảng lớn đều đã ra mắt một chợ agent trong khoảng ~18 tháng qua — đó là lớp phân
phối bạn có thể cắm vào, và là bằng chứng cho thấy mô hình "kệ hàng" là có thật:

- **Salesforce AgentExchange** (ra mắt **tháng 3/2025**): một chợ *đáng tin cậy* dành cho
  Agentforce; **200+ đối tác** lúc ra mắt; đạt **18,500 khách hàng** và **29,000+ giao dịch
  tích lũy** vào Q4 FY2026 — *"sản phẩm hữu cơ tăng trưởng nhanh nhất trong lịch sử
  Salesforce"* [[Salesforce](https://www.salesforce.com/news/press-releases/2025/03/04/agentexchange-announcement/)],
  [[CIO](https://www.cio.com/article/3837608/salesforces-agentexchange-targets-ai-agent-adoption-monetization.html)].
  — Lưu ý từ *"đáng tin cậy"*: ngay cả Salesforce cũng đang lần mò tìm một câu chuyện về
  niềm tin/thẩm định. Bạn có thể đi sâu hơn (kiểm định độ trung thành) so với một huy hiệu
  thẩm định đối tác.
- **OpenAI GPT Store**: **3M+ GPT tùy chỉnh**, nhưng khả năng kiếm tiền yếu — một khoản
  chia sẻ dựa trên mức độ tương tác vào khoảng **$0.03/cuộc hội thoại** (≈33,000 cuộc hội
  thoại chất lượng để kiếm $1,000/tháng) [[Fast.io](https://fast.io/resources/top-ai-agent-marketplaces/)].
  — Bài học: **các chợ không có chất lượng/kiểm chứng và kinh tế thực sự sẽ bị thương phẩm
  hóa về gần bằng không**. Mô hình royalty (tiền bản quyền) + kiểm định của bạn là liều
  giải độc.
- Google, Microsoft, AWS đều đã ra mắt chợ agent; giới bình luận mô tả đây là cuộc đua
  giành quyền sở hữu một *"thị trường lao động số trị giá nhiều nghìn tỷ đô"*
  [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)] (con số nghìn tỷ đó là
  câu chuyện của kênh phân phối, chứ không phải một TAM (quy mô thị trường) được đo lường —
  hãy viện dẫn cẩn thận).

## 3. Đà tăng trưởng của chuyên môn được kiểm chứng / nhân bản chuyên gia (luận điểm nguồn cung của bạn)

Những minh chứng đúng với luận điểm nhất — các startup nhân bản *phán đoán của chuyên gia
con người cụ thể* và đang được rót vốn cùng sử dụng:

- **Delphi** ("digital minds" — các chuyên gia biến mình thành chatbot): **Series A $16M**
  do **Sequoia** dẫn dắt (cùng Anthology Fund của Anthropic, Menlo, và các bên khác),
  **tháng 6/2025**; **tổng cộng $19M**; **doanh thu tăng 4×** kể từ tháng 11/2024
  [[Delphi/Sequoia](https://www.delphi.ai/blog/delphi-raises-16m-series-a-from-sequoia)],
  [[FinSMEs](https://www.finsmes.com/2025/06/delphi-raises-16m-in-series-a-funding.html)],
  [[Fast Company](https://www.fastcompany.com/91356476/delphi-ai-digital-mind)].
- **Cloneable** (theo dõi các chuyên gia con người trong công nghiệp nặng — năng
  lượng/điện lực/đường sắt — và tái triển khai quy trình làm việc của họ thành agent):
  **seed $4.6M** (Congruent Ventures), **tổng cộng $5.35M**; **ARR tăng 100×** từ tháng
  2 đến cuối 2025; khách hàng bao gồm **American Electric Power, Southern California
  Edison**; tuyên bố một tác vụ kỹ thuật 8 giờ → **<2 phút**
  [[Crunchbase News](https://news.crunchbase.com/venture/cloneable-cloning-expert-worker-knowledge-ai-infrastructure/)],
  [[BusinessWire](https://www.businesswire.com/news/home/20260423437615/en/)].
- **Personal AI, Kamoto.AI, Miria** — các bản nhân bản persona/chuyên gia với mô hình
  đăng ký thuê bao và cấp phép IP / chia sẻ doanh thu
  [[Entrepreneur](https://www.entrepreneur.com/science-technology/ai-clones-are-no-longer-science-fiction-theyre-real/494683)].

**Khoảng trống then chốt (đây chính là điểm chèn của bạn):** không bên nào công khai
**kiểm định độ trung thành với chuyên gia được nêu tên trên các trường hợp held-out (giữ
lại) kèm huy hiệu có thời hạn.** Họ bán bản nhân bản; họ không *đảm bảo và đo lường* rằng
nó tái tạo con người đó một cách trung thực. Đó chính xác là engine 07. Xem báo cáo về bức
tranh startup và báo cáo về hào lũy kiểm định.

## 4. Công cụ đánh giá / niềm tin AI (kề cận với hào lũy của bạn)

- **Gartner** dự báo **60% các nhóm kỹ thuật phần mềm** sẽ áp dụng các nền tảng đánh giá &
  quan sát AI vào **năm 2028**, tăng từ **18% năm 2025**
  [[qua getmaxim.ai](https://www.getmaxim.ai/articles/top-5-ai-evaluation-platforms-in-2026-2/)].
- Các nền tảng đánh giá (Maxim, Confident AI/DeepEval, Arize, Braintrust, LangSmith, Azure
  AI Foundry) cung cấp các chỉ số **độ trung thực / ảo giác (hallucination)** và **LLM làm
  giám khảo** — nhưng cho *chất lượng ứng dụng*, **chứ không phải cho "bản nhân bản này có
  khớp với một người cụ thể hay không."** Những điểm yếu đã biết của LLM làm giám khảo (thiên
  lệch vị trí, độ dài dòng, tự tôn) có nghĩa là **kiểm chứng vẫn là một bài toán mở và khó**
  [[DeepEval](https://deepeval.com/blog/llm-as-a-judge)],
  [[Microsoft](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/evaluating-ai-agents-can-llm%E2%80%91as%E2%80%91a%E2%80%91judge-evaluators-be-trusted/4480110)].

## 5. Kinh tế của router (đòn bẩy chi phí của bạn)

- **OpenRouter**: **định giá $500M** (tháng 4/2025), **huy động $40M**, xử lý **>$100M
  suy luận quy đổi năm**, kiếm tiền ở mức **~5%** của chi tiêu
  [[Sacra](https://sacra.com/c/openrouter/)].
- **LiteLLM**: **470k+ lượt tải**, có thể tự lưu trữ, người dùng doanh nghiệp (Netflix,
  Lemonade, RocketMoney) [[Xenoss](https://xenoss.io/blog/openrouter-vs-litellm)].

## 6. Những con số hỗ trợ cho bài pitch (phiên bản một đoạn)

> Thị trường AI tác tử (agentic AI) đạt ~**$9–12B năm 2026** tăng trưởng **35–50%/năm**
> (nhiều công ty nghiên cứu). Mọi
> nền tảng lớn đều đã ra mắt một chợ agent — chợ của Salesforce đạt **18,500 khách hàng
> trong chưa đầy một năm** — nhưng các chợ không có kiểm chứng sẽ bị thương phẩm hóa (GPT
> Store: ~**$0.03 mỗi cuộc hội thoại**). Nhân bản chuyên gia đang được rót vốn nhanh
> (**Delphi $16M/Sequoia**, **Cloneable ARR tăng 100×**) nhưng **chưa ai kiểm định rằng một
> bản nhân bản tái tạo con người được nêu tên một cách trung thực** — ngay cả khi tỷ lệ áp
> dụng đánh giá AI sắp tăng gấp ba lên **60% các nhóm kỹ thuật vào năm 2028**. Brain4All sở
> hữu khoảng trống đó: cổng kiểm định cho những twin chuyên gia được kiểm chứng.


# Bức tranh khởi nghiệp — Ai đang gần nhất, và khoảng trống thị trường

> Những công ty gần nhất với "terminal twin chuyên gia đã kiểm định" của Brain4All, được
> sắp xếp theo phân khúc, kèm vốn/lực kéo và — cột thực sự quan trọng — **liệu họ có kiểm
> định độ trung thành với một con người cụ thể được nêu tên hay không.** Mọi con số đều
> được liên kết và ghi ngày. Đây là bản quét toàn cảnh, không phải một sự chứng thực; hãy
> coi các con số vốn là số liệu được báo cáo, chưa qua kiểm toán.
>
> ⚠ **Kiểm tra thực tế ngay từ đầu:** kể từ bản nháp đầu tiên, không gian này đã trở nên
> *đông đúc hơn* và *gần hơn* với concept của Brain4All (Viven, IgniteTech, Interloom,
> Twinnin đều xuất hiện vào cuối 2025/2026). Khoảng trống thị trường vẫn còn mở — nhưng
> các bức tường đang thu hẹp lại, điều này làm tăng tính cấp bách của việc sở hữu lớp
> **kiểm định + royalty** trước tiên (xem §9).

## 1. Bản đồ: vẫn chưa ai ngồi ở điểm giao đầy đủ

Tầm nhìn Brain4All cần **bốn** thứ cùng một lúc mà hầu hết các startup chỉ làm được một
hoặc hai: **(A)** một agent runtime có thể tự lưu trữ (self-host), **(B)** nhân bản phán
đoán của một chuyên gia *được nêu tên*, **(C)** **kiểm định độ trung thành** với người đó
(đo lường được, có hạn dùng), **(D)** một kệ (registry) xuyên tổ chức với **royalty
(tiền bản quyền)**, chạy trên **dữ liệu của chính khách hàng**.

```
                         (A) self-host   (B) nhân bản    (C) KIỂM ĐỊNH    (D) registry +
                          runtime        chuyên gia      độ trung thành   royalty
                                         được nêu tên
Hermes / Brain4All          ●●●             ○ (dự kiến)    ○ (canh bạc)     ○ (dự kiến)
OpenClaw                    ●●●             ○              ○                ○
Viven                       ○               ●●● (nhân viên) ○               ○
IgniteTech MyPersonas       ○               ●●● (nhân viên) ○               ○
Interloom                   ○               ●● (tri thức    ○               ○
                                             ngầm)
Cloneable                   ● (triển khai)  ●●● (hiện trường)○              ○
Delphi                      ○               ●●● (creator)  ○                ● (chia doanh thu)
Coachvox                    ○               ●● (coach)     ○                ● (phí 10%)
Synthesia / HeyGen / Tavus  ○               ●● (avatar)    ○                ○
11x / Wonderful / Sierra    ○               ○ (vai trò,    ○                ○
                                             không phải người)
Braintrust / Galileo        ○               ○              ●● (chất lượng   ○
                                                            ứng dụng, không
                                                            phải trung thành
                                                            với người)
Salesforce AgentExchange    ○               ○              ○ (duyệt đối tác) ●● (marketplace)
```

**● mạnh · ○ yếu/không có.** Hàng có đủ cả bốn vẫn chưa tồn tại. Một vài công ty giờ đã
có ba trong bốn *trừ C* — đó chính xác là lý do C (kiểm định độ trung thành) là đòn bẩy.

---

## 2. Phân khúc A — "Bản sao số nhân viên/chuyên gia" cho doanh nghiệp (gần nhất, và mới nhất)

Những công ty này nhân bản tri thức của một *nhân viên/chuyên gia cụ thể được nêu tên*
cho doanh nghiệp. Đây là phân khúc dịch chuyển nhiều nhất kể từ bản nháp đầu tiên.

- **Viven** — **vòng seed $35M** (Khosla Ventures, Foundation Capital, FPV, Operator
  Collective), ra khỏi chế độ ẩn (stealth) vào **tháng 10/2025**. Xây dựng "bản sao số AI"
  (AI digital twins) — *những bản sao số cá nhân hóa cho nhân viên, ghi lại tri thức,
  quyết định và bối cảnh của họ* cho doanh nghiệp
  [[PRNewswire](https://www.prnewswire.com/news-releases/viven-emerges-from-stealth-with-35m-in-funding-to-bring-ai-digital-twins-to-the-enterprise-302585135.html)],
  [[StartupHub](https://www.startuphub.ai/ai-news/funding-round/2025/viven-raises-35m-to-advance-ai-digital-twin-technology)].
  → **Công ty được cấp vốn gần nhất với đơn vị nguyên thủy "twin" của bạn.** Nhưng đó là
  một sản phẩm doanh nghiệp được lưu trữ trên hosting; không có huy hiệu kiểm định độ
  trung thành công khai, không có kệ royalty xuyên tổ chức, không có câu chuyện chạy trên
  runtime của khách. Cổng kiểm định + royalty + self-host là điểm khác biệt của bạn.
- **IgniteTech — MyPersonas** — trình diễn tại **CES 2026**: các bản sao AI của nhân viên
  từ *video, giọng nói và tài liệu viết*; bản thế thân trả lời câu hỏi, trò chuyện qua
  video, phản hồi bằng **160 ngôn ngữ**
  [[Euronews](https://www.euronews.com/next/2026/01/07/ai-software-that-can-create-digital-clones-of-employees-unveiled-at-ces-2026)].
  → Trải nghiệm bản sao nhân viên; một lần nữa, không có đo lường/hạn dùng độ trung thành.
- **Interloom** — **$16.5M** (DN Capital, Bek, Air Street; sau vòng seed $3M tháng 3/2024).
  Ghi lại **tri thức ngầm** của công ty để cấp năng lượng cho các AI agent hiểu được quy
  trình làm việc của tổ chức
  [[Yahoo/Fortune](https://finance.yahoo.com/sectors/technology/articles/exclusive-interloom-startup-capturing-tacit-070000121.html)].
  → Cùng luận điểm *"mã hóa tri thức ngầm → agent"*; lấy quy trình làm trung tâm, không
  phải kiểm định độ trung thành với người được nêu tên.
- **Cloneable** — **vòng seed $4.6M** (Congruent), **tổng $5.35M**; quan sát bám theo các
  chuyên gia hiện trường trong lĩnh vực năng lượng/tiện ích/đường sắt, tái triển khai quy
  trình thành agent; **ARR tăng 100×** từ tháng 2 đến cuối 2025; khách hàng AEP, SCE
  [[Crunchbase News](https://news.crunchbase.com/venture/cloneable-cloning-expert-worker-knowledge-ai-infrastructure/)].
  → Nặng về dịch vụ ngành dọc (họ tự làm việc quan sát); bạn có thể là đường ray *kiểm
  định* theo chiều ngang mà họ sẽ đăng ký lên.
- **Tribal AI** — **vòng seed $10M**; agent "gốc metadata" (metadata-native) thông qua một
  *Metadata Fabric* ánh xạ metadata của các hệ thống doanh nghiệp trong một tổ chức
  [[SiliconANGLE](https://siliconangle.com/2026/05/20/tribal-ai-lands-10m-seed-funding-bring-metadata-native-agents-enterprise/)].
  → Lân cận (dữ liệu/metadata, không phải nhân bản người), nhưng cùng câu chuyện "ghi lại
  những gì trong đầu con người".
- **Twinnin** — một nền tảng AI *"gây tranh cãi"* đang mở vòng gọi vốn đầu tiên **nhắm mục
  tiêu ~$3M**, công khai **ký hợp đồng với các "twin"**
  [[Deadline](https://deadline.com/2026/05/ai-plaform-twinnin-funding-round-3-million-signs-up-twins-1236882734/)].
  → Gần nhất về *ngôn ngữ* (một marketplace của các "twin"); hãy để mắt tới nó — đây có
  thể là một nỗ lực sớm trong cùng một hạng mục, tranh cãi và tất cả.

> **Tín hiệu thị trường:** trong số các startup bản sao số có vòng gọi vốn gần nhất chốt
> vào 2025/26, **vòng trung bình là ~$56M — gấp khoảng 2× so với mức ~$27M** trung bình
> của các vòng trước 2024
> [[New Market Pitch](https://newmarketpitch.com/blogs/news/digital-twin-top-startups-fundraising)].
> Vốn đang tăng tốc chảy vào đúng phân khúc này.

## 3. Phân khúc B — Chuyên gia/creator tự nhân bản mình + kiếm tiền (tiền lệ về royalty)

Các nền tảng tiêu dùng/creator nơi một chuyên gia nhân bản *chính mình* và bán quyền truy
cập. Các **con số chia doanh thu của họ là tiền lệ rõ ràng nhất cho cỗ máy royalty của bạn.**

- **Delphi** — **$16M Series A / Sequoia** (tháng 6/2025), **tổng $19M**, **doanh thu 4×**
  kể từ tháng 11/2024; một "trí óc số" (digital mind) triển khai qua chat/SMS/WhatsApp/Slack/**giọng nói**;
  tỷ lệ ăn chia được ghi nhận gần nhất **~20% doanh thu thuê bao**
  [[Delphi/Sequoia](https://www.delphi.ai/blog/delphi-raises-16m-series-a-from-sequoia)],
  [[Personify comparison](https://personify.fyi/blog/delphi-vs-coachvox/)].
- **Coachvox** — nhân bản các coach/consultant/diễn giả để mở rộng vượt khỏi 1:1; tỷ lệ ăn
  chia **10% mỗi giao dịch + phí Stripe**; định hướng tạo khách hàng tiềm năng (lead-gen)
  [[Personify comparison](https://personify.fyi/blog/delphi-vs-coachvox/)].
- **BuddyPro**, **Personify** — các nền tảng "nhân bản chuyên gia AI / coaching" tương đương
  [[BuddyPro](https://buddypro.ai/blog/buddypro-vs-coachvox-vs-delphi-alternatives)].
- **Personal AI**, **Kamoto.AI**, **Miria** — nhân bản persona/chuyên gia; mô hình thuê bao
  + cấp phép IP / chia doanh thu; Miria (thành lập tháng 10/2025) nhắm vào giới điều
  hành/chuyên gia/creator với ~20 hồ sơ
  [[Entrepreneur](https://www.entrepreneur.com/science-technology/ai-clones-are-no-longer-science-fiction-theyre-real/494683)].

**Đọc ra:** thị trường đã định giá **tỷ lệ chia nền-tảng-với-chuyên-gia (10–20%)** — cơ
chế royalty của bạn không mới *về mặt kinh tế*; cái mới là gắn nó vào một twin **đã kiểm
định, xuyên tổ chức, chạy trên dữ liệu của khách** thay vì một chatbot creator được lưu
trữ trên hosting.

## 4. Phân khúc C — Hạ tầng avatar / persona tương tác ("gương mặt + giọng nói")

Không phải nhân bản phán đoán, mà là lớp *hiện thân* mà các chuyên gia vốn đã trả tiền —
những đối tác tiềm năng hoặc một giao diện front-end cho twin.

- **Synthesia** — **huy động $500M+**, **$200M Series E ở định giá $4B** (cuối 2025),
  **$146M ARR**, 70% của Fortune 100 [[Sacra](https://sacra.com/c/heygen/)].
- **HeyGen** — **$60M Series A** (tháng 6/2024), **~$95–100M ARR** vào cuối 2025
  [[Sacra](https://sacra.com/c/heygen/)].
- **Tavus** — **$40M Series B / CRV** (tháng 11/2025), **tổng ~$64M**; avatar video hội
  thoại thời gian thực (PALs) [[Sacra](https://sacra.com/c/tavus/)].
- **D-ID** — 280K lập trình viên.

**Đọc ra:** avatar là *hiện thân* hàng hóa phổ thông, không phải phán đoán đã kiểm định.
Chỉ liên quan như một **lớp da giao vận (delivery skin)** cho một twin — đừng nhầm một bản
sao đầu-biết-nói với một twin chuyên gia đã kiểm định độ trung thành.

## 5. Phân khúc D — AI agent ngành dọc / "nhân viên AI" (nhân bản một *vai trò*, không phải một con người)

Lát cắt được cấp vốn tốt nhất của AI dạng agentic — nhưng họ tái tạo một *chức năng công
việc*, không phải một *cá nhân được nêu tên*, và không kiểm định độ trung thành với bất kỳ ai.

- **AI agent ngành dọc = 55.7% vốn agentic-AI đã công bố — $2.64B trên 30 thương vụ**;
  Pháp lý/Bảo hiểm/Xây dựng/Y tế = 72.6% trong số đó
  [[New Market Pitch](https://newmarketpitch.com/blogs/news/vertical-ai-funding-analysis)].
- **Wonderful** — **$250M qua hai vòng trong 4 tháng**, **định giá $2B**; các agent chăm
  sóc khách hàng đa ngôn ngữ
  [[funding tracker](https://aifundingtracker.com/top-ai-agent-startups/)].
- **11x.ai** — **$24M Series A / Benchmark**; "công nhân số tự động" (SDR)
  [[TechCrunch](https://techcrunch.com/2024/09/16/ai-digital-employee-startup-11xai-raises-24m-led-by-benchmark)].
- (Cộng thêm Sierra, Cognition, Decagon, v.v. — các agent thay thế vai trò.)

**Đọc ra:** những công ty này chứng minh doanh nghiệp *mua* "AI làm được một công việc".
Lợi thế của bạn: bạn không bán một agent-vai-trò chung chung — bạn bán một *bản sao đã
kiểm định của một chuyên gia đáng tin cậy cụ thể*, thứ chỉ huy mức giá theo sự khan hiếm
mà một agent-vai-trò hàng hóa phổ thông không thể có.

## 6. Phân khúc E — Marketplace agent (lớp phân phối/kệ hàng)

- **Salesforce AgentExchange** (tháng 3/2025): marketplace "đáng tin"; **18,500 khách hàng,
  29,000+ thương vụ** tính đến Q4 FY2026 — sản phẩm tăng trưởng nhanh nhất trong lịch sử
  Salesforce
  [[Salesforce](https://www.salesforce.com/news/press-releases/2025/03/04/agentexchange-announcement/)].
  "Sự tin cậy" của nó = **duyệt đối tác**, không phải độ trung thành được đo lường.
- **AWS Marketplace / Google / Microsoft** các cửa hàng agent; **GPT Store** (3M+ GPT,
  kinh tế ~$0.03/cuộc hội thoại) [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)].
- Thị trường "AI agent" rộng hơn được trích dẫn ở mức **$5.25B (2024) → $7.84B (2025) →
  ~$52.6B vào 2030**, **10,000+ agent tùy chỉnh được xuất bản mỗi tuần**
  [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)] (khung "AI agent" rộng
  — lớn hơn và lỏng lẻo hơn các con số "agentic AI" trong chương thị trường và con số; hãy
  trích dẫn khoảng, không phải một điểm).

**Đọc ra:** các marketplace tồn tại và mở rộng quy mô, nhưng tất cả đều duyệt *đối
tác/bảo mật*, không cái nào kiểm định *độ trung thành với một con người*. Điểm khác biệt
của kệ (registry) của bạn là **huy hiệu + hạn dùng + royalty**, không phải thêm một bề mặt
đăng danh sách nữa.

## 7. Phân khúc F — Đánh giá (eval) / tin cậy / quan sát vận hành (observability) (bộ công cụ lân cận với kiểm định)

Những người đang xây dựng *bộ máy* gần nhất với cổng kiểm định của bạn — nhưng cho **chất
lượng ứng dụng, không phải độ trung thành với một con người.**

- **Braintrust** — **$80M Series B / Iconiq** (a16z, Greylock, Elad Gil), **định giá $800M**;
  quan sát vận hành + đánh giá, ảo giác/trôi dạt/thoái lui, các bộ chấm điểm LLM làm giám
  khảo [[SiliconANGLE](https://siliconangle.com/2026/02/17/braintrust-lands-80m-series-b-funding-round-become-observability-layer-ai/)].
- **Galileo AI** — đánh giá/quan sát vận hành với **Luna-2**, các mô hình eval nhỏ độc
  quyền để chấm điểm độ trễ thấp; các chỉ số trung thực/ảo giác được hậu thuẫn bởi nghiên
  cứu [[parsers.vc](https://parsers.vc/startup/galileo.ai/)] *(các con số vốn không nhất
  quán giữa các nguồn — hãy coi là chưa được xác minh)*.
- (Cộng thêm Arize, Confident AI/DeepEval, Maxim, LangSmith.) Gartner: tỷ lệ áp dụng nền
  tảng đánh giá **18% (2025) → 60% đội ngũ kỹ thuật (2028)** (xem chương thị trường và con số).

**Đọc ra:** những công cụ này đo *"đầu ra có đúng/trung thực với sự thật nền không"* —
**không phải "bản sao này có tái tạo được phán đoán của Bác sĩ X không."** Công nghệ trôi
dạt/chấm điểm của họ chính xác là thứ mà cỗ máy kiểm định của bạn cần; **hợp tác hoặc xây
dựng trên nền của họ** thay vì cạnh tranh. Và lưu ý các thiên lệch đã biết của LLM làm
giám khảo (xem chương lợi thế phòng thủ kiểm định) nghĩa là chỉ số *độ trung thành với một
con người* vẫn còn là R&D chưa được xây dựng — lợi thế phòng thủ (moat) của bạn.

---

## 8. Phép so sánh duy nhất quan trọng

| Công ty | Self-host | Nhân bản một người **được nêu tên** | **Kiểm định độ trung thành** (đo lường được, có hạn dùng) | Royalty / chia doanh thu | Chạy trên dữ liệu của khách |
|---|:--:|:--:|:--:|:--:|:--:|
| **Brain4All (mục tiêu)** | ✔ | ✔ | ✔ **(moat)** | ✔ | ✔ |
| Viven | ✘ | ✔ | ✘ | ✘ | ◐ |
| IgniteTech MyPersonas | ✘ | ✔ | ✘ | ✘ | ✘ |
| Cloneable | ◐ | ✔ | ✘ | ✘ | ✔ |
| Interloom | ✘ | ◐ (tri thức ngầm) | ✘ | ✘ | ✔ |
| Delphi | ✘ | ✔ | ✘ | ✔ (~20%) | ✘ |
| Coachvox | ✘ | ✔ | ✘ | ✔ (10%) | ✘ |
| Synthesia/HeyGen/Tavus | ✘ | ◐ (avatar) | ✘ | ✘ | ✘ |
| 11x / Wonderful / Sierra | ✘ | ✘ (vai trò) | ✘ | ✘ | ◐ |
| Braintrust / Galileo | ◐ | ✘ | ◐ (chất lượng ứng dụng) | ✘ | ✔ |
| Salesforce AgentExchange | ✘ | ✘ | ◐ (duyệt đối tác) | ◐ | ✔ |

**Cột "kiểm định độ trung thành" trống trơn với tất cả mọi người.** Đó là toàn bộ luận đề
gói gọn trong một cột.

## 9. Khoảng trống thị trường, phát biểu lại (sắc bén hơn, hậu-Viven)

> **Không công ty nào đo lường liệu một AI twin có tái tạo trung thành phán đoán của một
> *chuyên gia cụ thể được nêu tên* trên các trường hợp giữ lại hay không, cấp một huy hiệu
> tin cậy *có hạn dùng*, giám sát sự trôi dạt, và thanh toán *royalty* xuyên các tổ chức
> trong khi twin chạy trên *dữ liệu/runtime của chính khách hàng*.**

Điều đã thay đổi kể từ bản nháp 1: **Viven, IgniteTech, Interloom, Twinnin** giờ chiếm ô
"nhân bản một người được nêu tên cho doanh nghiệp" mà từng thưa thớt. Vậy nên "chúng tôi
nhân bản chuyên gia" **không còn là điểm khác biệt** — nhiều đội đã được cấp vốn cũng nói
vậy. Vùng đất phòng thủ còn lại của bạn thu hẹp về những **phần khó, chưa được xây dựng**:

1. **Đo lường kiểm định/độ trung thành** (engine 07) — vẫn chưa ai làm. Đám đông eval đo
   chất lượng ứng dụng; đám đông twin không đo gì cả.
2. **Huy hiệu có hạn dùng + trôi dạt → tái kiểm định** — một cơ chế doanh thu định kỳ mà
   không ai trong số họ có.
3. **Royalty xuyên tổ chức + xuất xứ (lineage)** trên một twin đã kiểm định (Delphi/Coachvox
   có chia doanh thu, nhưng chỉ trên các chatbot đơn-creator được lưu trữ trên hosting,
   không phải một kệ (registry) xuyên tổ chức đã kiểm định).
4. **Chạy trên runtime/dữ liệu của chính khách hàng** (self-host) — đám đông twin được lưu
   trữ trên hosting; đây là lợi thế cấu trúc của bạn + Hermes.

**Hàm ý cho chiến lược:** dẫn dắt bằng **kiểm định**, không phải "twin". Nếu bài chào hàng
là "chúng tôi tạo twin chuyên gia", giờ bạn là một trong hàng chục công ty. Nếu là "chúng
tôi là *đường ray kiểm định độ trung thành + royalty* cho twin chuyên gia — bao gồm cả
những twin mà Viven/Cloneable/Delphi tạo ra", bạn là một hạng mục chỉ có một. (So với
chương lợi thế phòng thủ kiểm định.)

## 10. Những gì nên học lỏm, và các rủi ro

**Học lỏm:**
- **Con số chia doanh thu của Delphi/Coachvox (10–20%)** — một mỏ neo giá đã được kiểm
  chứng cho royalty.
- **Điểm nêm (wedge) chuyên-gia-sắp-nghỉ-hưu của Cloneable** — ghi lại tay nghề *sắp bước
  ra khỏi cửa*.
- **Bộ máy trôi dạt/chấm điểm của Braintrust/Galileo** — xây bộ chấm điểm độ trung thành
  của bạn lên trên đó, đừng phát minh lại đường ống eval.
- **Khung twin-doanh-nghiệp của Viven** — chứng minh người mua doanh nghiệp tồn tại ở mức
  tin tưởng của một vòng seed $35M.

**Rủi ro (sắc bén hơn bây giờ):**
1. **Ô nhân bản đang lấp đầy nhanh.** Viven ($35M) và vòng trung bình của bản sao số
   (~$56M) nghĩa là các đội được cấp vốn dồi dào có thể gắn thêm một "điểm tin cậy" trước
   khi bạn kịp ra một chuẩn kiểm định thực sự. **Tốc độ ở engine 07 là toàn bộ cuộc chơi.**
2. **Một kiểm định giả "đủ tốt"** (ví dụ một "% độ trung thành" do LLM làm giám khảo) có
   thể biến huy hiệu thành hàng hóa phổ thông nếu bạn không làm cho huy hiệu của mình có
   tính phòng thủ (chuyên gia chấm lại một cách mù + đồng thuận khách quan, không chỉ là
   một điểm số LLM — xem chương lợi thế phòng thủ kiểm định).
3. **Hàng hóa hóa marketplace** (GPT Store → $0.03/cuộc hội thoại) — hãy cạnh tranh trên
   *sự khan hiếm + xác minh + royalty*, không bao giờ trên khối lượng danh sách.
4. **Sự đồng thuận từ phía nguồn cung** — được giảm nhẹ bởi góc độ chuyên-gia-sắp-nghỉ-hưu
   (bên dưới).

## 11. Cơn gió thuận từ phía cung (dùng cái này trong bài chào hàng)

Luận điểm "ghi lại tay nghề trước khi nó bước ra khỏi cửa" có một con số nhân khẩu học
cứng rắn: **~10,000 người thuộc thế hệ Baby Boomer nghỉ hưu mỗi ngày ở Mỹ**, khiến việc
bảo tồn tri thức tổ chức trở nên cấp bách — cơn gió thuận rõ rệt mà các nhà đầu tư đang
hậu thuẫn trên khắp Interloom, Tribal AI, Cloneable và Viven
[[Yahoo/Fortune on Interloom](https://finance.yahoo.com/sectors/technology/articles/exclusive-interloom-startup-capturing-tacit-070000121.html)].
Điều này trả lời trực tiếp cho "bức tường nguồn cung" của tầm nhìn (xem chương tầm nhìn
twin terminal, §5): các chuyên gia sắp nghỉ hưu sẵn sàng đồng thuận khi lựa chọn thay thế
là chuyên môn của họ biến mất.


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
| `models/` | `api.py` | Hợp đồng Pydantic (điều khiển `/docs`, `/openapi.json`) |
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

### ⚠ Kiểm tra thực tế về ngôn ngữ so với kế hoạch đã nêu

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
| Runtime agent, bộ nhớ, skill, MCP, sandbox | ✔ qua Hermes |
| Định tuyến đa nhà cung cấp | ✔ qua 9router |
| Self-host + cloud + mã nguồn enterprise được bảo vệ | ✔ đã thiết kế (phân tách open-core) |
| **Xưởng biên soạn** skill/twin | ◐ một phần (skill + trình soạn thảo Kanban) |
| **Cổng kiểm định độ trung thành** (moat) | ✘ chưa bắt đầu |
| Danh tính / chống mạo danh | ✘ chưa bắt đầu |
| Kệ (registry) với huy hiệu có hạn dùng | ✘ chưa bắt đầu |
| Lineage, royalty (tiền bản quyền), thanh toán liên tổ chức | ✘ đã có thanh toán enterprise; royalty/lineage thì chưa |
| Giám sát trôi dạt (drift) → tái kiểm định | ✘ chưa bắt đầu |

**Điểm mấu chốt:** các động cơ "hàng hóa phổ thông" (bộ não, thực thi, kết nối, và phần
lớn điều phối) phần lớn được **thuê hoặc đã xây dựng sẵn**. Mọi động cơ mà tầm nhìn đánh
dấu là *lợi thế phòng thủ (moat)* — **kiểm định, danh tính, vòng lặp cải tiến,
thương mại/royalty** — đều là mảnh đất trống. Đó chính là nơi đáng để đầu tư, và là nơi
roadmap nên tập trung.


# Công nghệ cốt lõi & Đối thủ trực tiếp

> Nền tảng bạn đang xây dựng trên đó (Hermes + một bộ định tuyến), những gì nó làm
> được và không làm được, các hàm ý về giấy phép của nó, và những runtime mã nguồn mở
> mà bạn sẽ bị đem ra so sánh. Mọi tuyên bố từ nguồn bên ngoài đều có liên kết và ghi ngày.

## 1. Hermes Agent (Nous Research) — động cơ của bạn

**Nó là gì.** Một AI agent tự cải thiện, mã nguồn mở, **giấy phép MIT**, ra mắt
**tháng 2 năm 2026**. Đây chính xác là thứ mà Brain4All bọc lại (chính là "ứng dụng
dashboard Hermes CLI gốc" của repo).

**Năng lực** (theo trang chính thức
[user-stories](https://hermes-agent.nousresearch.com/docs/user-stories) và các bài
phân tích độc lập
[[awesomeagents.ai](https://awesomeagents.ai/news/nous-research-hermes-agent-open-source-memory/)],
[[aiengineerinsights.com](https://aiengineerinsights.com/blog/hermes-agent-nous-research-guide/)]):

- **Skill tự cải thiện** — viết ra các tệp `SKILL.md` tái sử dụng được từ những bài
  toán đã giải; "cùng một tác vụ sẽ nhanh hơn và rẻ hơn ở lần thứ hai."
- **Bộ nhớ bền vững 3 tầng** — các sự kiện lâu bền (`MEMORY.md`), tìm kiếm theo phiên,
  và các skill quy trình, với tìm kiếm toàn văn SQLite **FTS5**.
- **Vòng lặp học tập năm giai đoạn** — thực thi → đánh giá → trích xuất các mẫu tái sử
  dụng được thành skill có tên → tinh chỉnh → truy xuất cho các tác vụ mới.
- **Công cụ** — thực thi mã, thao tác tệp, duyệt/nghiên cứu web, tích hợp **MCP**
  (Model Context Protocol).
- **Năm backend sandbox** — local, Docker, SSH, Singularity, Modal.
- **Nhắn tin đa nền tảng** — Telegram, Discord, Slack, WhatsApp, Signal, Email,
  CLI, và nhiều nền tảng khác thông qua các adapter.
- **Cron ngôn ngữ tự nhiên** ("mỗi ngày trong tuần lúc 9 giờ sáng, tóm tắt hộp thư của tôi").
- **Cổng phê duyệt** ("chế độ hội thoại" yêu cầu con người ký duyệt trước khi dùng
  công cụ) — đây chính là lõi phê duyệt Hermes mà Brain4All giữ lại.

**Độ trưởng thành.** Đến phiên bản v0.18.2 (tháng 7 năm 2026), dự án báo cáo hàng trăm
issue đã đóng, **370+ người đóng góp, và không có lỗi P0 nào còn mở**
[[awesomeagents.ai](https://awesomeagents.ai/news/nous-research-hermes-agent-open-source-memory/)].
Đủ mức độ production để xây dựng lên trên; nhưng vẫn thay đổi nhanh (phiên bản 0.x).

**Hệ sinh thái bạn được thừa hưởng miễn phí:**

- `agentskills.io` — một **chuẩn skill** cộng đồng; `awesome-hermes-agent` tuyển chọn
  các skill theo chuẩn này.
- **Hermes Atlas** (`hermesatlas.com`) — một bản đồ hệ sinh thái được cào dữ liệu, có
  đánh giá sao.
- **Hermify** — một dịch vụ **lưu trữ được quản lý có trả phí** ("mang API key +
  Telegram bot của bạn tới"). Lưu ý: Nous đã kiếm tiền từ việc lưu trữ — nên "Hermes
  được lưu trữ" là một làn đường cạnh tranh, không phải vùng nước trống. Điểm khác biệt
  của bạn là **kiểm định/kệ (registry)**, không phải lưu trữ.

### Các hàm ý về giấy phép MIT (quan trọng, và thuận lợi)

MIT là giấy phép **dễ dãi (permissive) tối đa**: bạn có thể dùng, sửa đổi và phân phối
Hermes — **kể cả trong một sản phẩm thương mại đóng** — với về cơ bản chỉ một nghĩa vụ:
giữ nguyên thông báo bản quyền + giấy phép
[[tổng quan giấy phép](https://dev.to/juanisidoro/open-source-licenses-which-one-should-you-pick-mit-gpl-apache-agpl-and-more-2026-guide-p90)].
Hệ quả cho Brain4All:

- ✔ Bạn có thể hợp pháp xây dựng một **tầng doanh nghiệp/đám mây độc quyền** lên trên
  Hermes và giữ tầng đó đóng. Đây chính xác là điều mà mô hình chia tách open-core giả định.
- ✔ Bạn **không** bị buộc phải mở mã nguồn của chính mình (khác với AGPL/GPL).
- ⚠ Mặt trái: MIT cũng không cho *bạn* sự bảo vệ nào — bất kỳ ai (kể cả một cloud
  hyperscaler hay chính Nous) đều có thể bọc Hermes. **Lợi thế phòng thủ (moat) của bạn
  không thể là "chúng tôi đã bọc Hermes."** Nó phải là cổng kiểm định, dữ liệu nghề,
  danh tính, và lineage royalty (các động cơ 05–07, 09). Điều này nhất quán với tài
  liệu tầm nhìn.
- • **Hành động:** giữ một tệp ghi công/NOTICE sạch cho Hermes và bộ định tuyến, cùng
  một bảng kiểm kê giấy phép của các phụ thuộc, trước khi phân phối công khai. Repo OSS
  của chính bạn vẫn cần chọn một giấy phép — README nói thẳng *"Chọn và thêm một giấy
  phép trước khi phân phối công khai."* Xem tài liệu về mô hình kinh doanh và cấp phép.

## 2. Bộ định tuyến ("9router") và bức tranh định tuyến

"9router" của Brain4All là một bộ định tuyến LLM (một tiến trình chạy trên `:20128`)
định tuyến tới nhiều nhà cung cấp — cùng công việc như **OpenRouter** (được lưu trữ)
hay **LiteLLM** (proxy tự lưu trữ được). Dù "9router" là một dự án riêng biệt hay chỉ
là bí danh của bạn, đây là bức tranh mà nó nằm trong đó:

- **OpenRouter** — bộ tổng hợp được lưu trữ; kiếm tiền bằng cách lấy **~5% trên chi
  tiêu inference**; **định giá $500M** sau vòng **Series A $28M** (Menlo Ventures,
  tháng 4 năm 2025), tổng cộng huy động **$40M**; xử lý **>$100M inference quy đổi hằng
  năm** tính đến tháng 5 năm 2025 (tăng từ ~$19M cuối năm 2024)
  [[Sacra](https://sacra.com/c/openrouter/)].
- **LiteLLM** — proxy mã nguồn mở, **tự lưu trữ được**; **470k+ lượt tải**; người dùng
  trong môi trường production bao gồm Netflix, Lemonade, RocketMoney
  [[xenoss.io](https://xenoss.io/blog/openrouter-vs-litellm)].

**Đọc theo góc chiến lược:** định tuyến là một **động cơ hàng hóa phổ thông (01/04
trong tầm nhìn)** — đừng cố xây dựng để vượt OpenRouter. Hãy dùng một bộ định tuyến
tự lưu trữ được (loại LiteLLM) để phiên bản doanh nghiệp có thể chạy **hoàn toàn on-prem
với chính khóa nhà cung cấp của khách hàng** — đó là một yêu cầu doanh nghiệp thực sự
(lưu trú dữ liệu, giữ khóa) và là lý do các công ty chọn bạn thay vì một stack chỉ có
lựa chọn được lưu trữ. Mẫu "các tầng định tuyến thông minh" (mô hình rẻ cho công việc
máy móc, mô hình cao cấp cho việc mơ hồ) đã phổ biến sẵn trong hệ sinh thái Hermes và
là một câu chuyện chi phí hay ho, chứ không phải một lợi thế phòng thủ (moat).

## 3. OpenClaw — đối thủ gần gũi nhất về mặt triết lý

**Nó là gì.** Một **gateway kết nối các ứng dụng chat với AI coding/agent**, mã nguồn
mở, tự lưu trữ; **ưu tiên cấu hình (config-first)** (viết một `SOUL.md`, chạy một lệnh,
agent hoạt động ngay — "không Python, không chain, không graph"); **160,000+ sao
GitHub**; bộ nhớ bền vững qua markdown + SQLite; định tuyến đa agent qua một gateway
[[Milvus guide](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md)],
[[SFAI Labs](https://sfailabs.com/guides/openclaw-ai-agent-framework)],
[[freeCodeCamp](https://www.freecodecamp.org/news/how-to-build-and-secure-a-personal-ai-agent-with-openclaw/)].

**Nó trùng lặp và khác biệt với Hermes/Brain4All như thế nào:**

| | Hermes / Brain4All | OpenClaw |
|---|---|---|
| Mô hình cấu hình | Skill + hồ sơ + UI | `SOUL.md` ưu tiên cấu hình |
| Bộ nhớ | MEMORY.md + FTS5 + skill | markdown + SQLite |
| Phân phối | nhắn tin + không gian làm việc web | gateway nhắn tin |
| Tự cải thiện | ✔ tự viết skill của mình | hạn chế |
| **Kiểm định / độ trung thành của twin** | lợi thế phòng thủ bạn dự kiến | ✘ không có |

**Đọc theo góc chiến lược:** OpenClaw chứng minh có *nhu cầu khổng lồ* đối với các
**agent cá nhân tự lưu trữ** (160k sao là con số cực lớn). Nhưng giống Hermes, nó cạnh
tranh về **runtime và sự tiện lợi**, không phải về **các twin chuyên gia đã được kiểm
chứng**. Cả OpenClaw lẫn Hermes đều không làm kiểm định độ trung thành. Khoảng trống đó
chính là luận điểm của bạn — và nó vẫn đang bỏ ngỏ.

## 4. Lĩnh vực runtime agent mã nguồn mở rộng hơn (những đối thủ bạn bị so sánh cùng)

Người mua khi đánh giá các nền tảng "xây dựng AI agent" sẽ gọi tên những cái tên này.
Hãy định vị bản thân là *"tầng twin-chuyên-gia đã được kiểm chứng,"* chứ không phải
*"thêm một framework agent nữa."*

| Dự án | Hình thái | Cách kiếm tiền |
|---|---|---|
| **LangGraph / LangChain** | framework/điều phối cho lập trình viên | LangSmith (quan sát/đánh giá) SaaS + doanh nghiệp |
| **CrewAI**, **AutoGen** | framework đa agent | các tầng doanh nghiệp/đám mây |
| **OpenHands** | agent lập trình tự chủ | đám mây + doanh nghiệp |
| **Dify**, **Flowise**, **n8n** | trình xây dựng agent/luồng công việc low-code | **open-core** (n8n dùng fair-code "dùng nội bộ miễn phí, cấm bán lại") |
| **Cline** | agent lập trình trong IDE | phần lớn tự mang khóa (BYO-key) |

Hai điều rút ra cho chiến lược:

1. **Mẫu thương mại phổ biến là open-core** (lõi tự lưu trữ miễn phí + đám mây/doanh
   nghiệp trả phí) — đúng như kế hoạch dự định. Xem tài liệu về mô hình kinh doanh và
   cấp phép.
2. **Ai cũng cạnh tranh ở việc "xây dựng agent." Không ai cạnh tranh ở việc "kiểm định
   rằng agent này tái tạo trung thành một chuyên gia con người cụ thể có tên."** Đó là
   câu nói phân biệt Brain4All với toàn bộ danh sách này.


# Lợi thế phòng thủ từ kiểm định — Biến Engine 07 thành hiện thực

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


# Mô hình kinh doanh & Cấp phép

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
- ⚠ **Tương thích giấy phép:** nếu bạn chọn AGPL cho lõi, hãy xác nhận tính tương thích
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


# Kiến trúc tích hợp & Kế hoạch triển khai

Phần này ghi lại cách hệ thống hiện tại tích hợp runtime Hermes và bộ định tuyến LLM
(LLM router), các đặc tính hiệu năng của việc tích hợp đó, cách các profile (cách ly theo
từng agent) hoạt động, và kiến trúc mục tiêu được khuyến nghị cho sản phẩm thương mại.

## Tích hợp hiện tại: một tiến trình mở rộng Hermes

Ứng dụng mã nguồn mở không chạy một máy chủ riêng gọi Hermes qua mạng. Nó import chính
ứng dụng web-server của Hermes và đăng ký các tuyến (route) tương thích của mình lên đó,
rồi phục vụ ứng dụng kết hợp trên cổng 8642. Trên thực tế, tiến trình đang chạy là máy chủ
web của Hermes cộng với bề mặt quản trị của Brain4All, trong một tiến trình Python duy nhất
dùng chung một môi trường ảo. Bộ định tuyến LLM chạy như một tiến trình riêng trên cổng
20128 và được truy cập qua HTTP.

Vì Brain4All dùng chung tiến trình và môi trường, nó tích hợp với Hermes qua nhiều kênh
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


# Lộ trình sản phẩm — Từ Studio đến Terminal

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
  OpenRouter/LiteLLM hay fork lõi của Hermes; hãy mở rộng từ `brain4all`, theo `AGENTS.md`.

## Quy tắc trình tự một dòng

> Chứng minh tấm huy hiệu (Giai đoạn (Phase) 1) → làm cho nó có sức nặng (Giai đoạn
> (Phase) 2) → để nó đi kèm royalty mà lưu chuyển (Giai đoạn (Phase) 3) → biến nó thành
> chuẩn mực (Giai đoạn (Phase) 4). Mọi thứ hàng hóa, hãy đi thuê. Mọi thứ thuộc lợi thế
> phòng thủ (05/06/07/09), hãy tự xây — bắt đầu bằng bộ chấm độ trung thành.


# Hermes Agent — Danh mục use-case đầy đủ & Đối chiếu

> Nguồn: [hermes-agent.nousresearch.com/docs/user-stories](https://hermes-agent.nousresearch.com/docs/user-stories),
> lấy về 2026-07-24. Trang này liệt kê **262 story** thuộc **15 danh mục** từ
> **11 kênh nguồn**. **220** story được ghi lại đầy đủ chi tiết ở đây; khoảng ~42 story
> cuối bị cắt bởi giới hạn độ dài nội dung của trang (được ghi chú ở cuối). Mỗi mục bên
> dưới là một bản ghi cô đọng nhưng trung thực: tiêu đề, agent làm gì, và các
> công cụ/mô hình/con số được nêu tên. **Các mục C–E chứa phần phân tích** — những story
> nào gần nhất với Twin Terminal, nơi Hermes và OpenClaw xuất hiện cùng nhau, và điều đó
> có ý nghĩa gì với Brain4All.

## Cấu trúc trang (nguyên văn)

- **262 story · 15 danh mục · 11 nguồn.**
- **Danh mục (kèm số lượng):** Dev Workflow (65) · Personal Assistant (44) ·
  Integrations (26) · Meta & Ecosystem (21) · Creative (19) · Business Ops (16) ·
  Cost Optimization (13) · Content Creation (11) · Research (9) · Enterprise (9) ·
  Messaging (8) · Privacy & Self-Hosted (8) · General (6) · Trading & Markets (5) ·
  Marketing (2).
- **Nguồn (kèm số lượng):** Discord (116) · X/Twitter (42) · GitHub (38) · Blog (20) ·
  YouTube (17) · Reddit (15) · GitHub Gist (4) · Hacker News (4) · LinkedIn (3) ·
  Podcast (2) · Product Hunt (1).

---

## A. Danh mục, theo từng nhóm

> Ghi chú: danh mục 220 story dưới đây được giữ nguyên tiếng Anh theo nguồn; phần phân tích (mục C–E) đã được dịch đầy đủ.

> Định dạng: **#N — Tiêu đề** · nội dung; *công cụ/mô hình/con số* (nguồn).

### Dev Workflow — Quy trình phát triển (tự cải thiện, memory kernel, đa agent, tooling)

- **#3** — Local-first cognition layer; building a memory/cognition layer for Hermes (Discord).
- **#10** — Codex watches Hermes agent-to-agent workflows live; runtime monitor catches breaks, live fixes; *Codex, GPT-5.4 extra-high* (X).
- **#12** — "Converse mode"; plugin makes agent talk before executing tools (approval-first) (Discord).
- **#13** — Token profiling; dashboard finds *73% of every API call is fixed overhead* across 6 request dumps; *Hermes v0.6.0* (GitHub).
- **#25** — LaTeX→Unicode math rendering in the TUI (GitHub PR).
- **#29** — "Hadn't coded in 20 years"; vibe-coding via *Claude Code + Hermes* (Discord).
- **#31** — 200–400h memory kernel; 3-layer *L1 Hindsight / L2 Graphiti / L3 MemPalace*; project **BRAINSTACK** (Discord).
- **#41** — `hermes mcp-server` exposes 9 Hermes tools to *Claude Desktop, Cursor* (Discord).
- **#43** — Nous runs **12 Hermes instances in parallel daily** to build Hermes; *900k+ compute-seconds, 5B+ tokens*; now a top-100 GitHub repo (X, @Teknium).
- **#50** — Hooks that swap in better tools at every agent run (Discord).
- **#51** — RCA script audited **129 sessions/23 days → 112 had ≥1 approval-gate violation** (GitHub).
- **#52** — CCD multi-agent pod on M2 Ultra; *Mem0 + Qdrant*, per-agent profiles (GitHub).
- **#53** — Custom kernel: "the LLM never touches the disk"; Python compiles semantic signals into *SQLite FTS5 graph* (Discord).
- **#57** — Agent editing its own internals; worry about updates overwriting (Discord).
- **#58** — Independently built a stack, converged on Hermes (same self-improve/memory/skills design); **300 PRs in a week** (X).
- **#64** — Nightly config+DB backup to GitHub; multi-agent management (Discord).
- **#67** — Skill-audit skill that improves itself on cron (sandboxed self-improvement loop) (Discord).
- **#69** — Hermes on VPS, "phones home" over *Tailscale*; scoped tags; "yolo mode, human owns boundaries" (Discord).
- **#74** — "Day 10: it knows my codebase better than I do"; internalized prefs by 5th iteration (X).
- **#75** — 22k-line memory kernel; *temporal context graph in SQLite* with decay/promotion/supersession (Discord).
- **#78** — Local network **Kanban** so agents see task state; project **Goban** (Discord).
- **#84** — Agent auto-acts on file-change events (X).
- **#86** — **Cartographer** (memory: semantic wiring, emotional topology → temporal graph in SQLite) + **Agent IRC** (real-time chat between Hermes, Claude, Codex, OpenCode, Gemini) (Discord).
- **#88** — Multi-agent auto-build: *GPT-5.4 plans → MiniMax M2.7 codes → local Qwen 35B QA → repair → ship* (X).
- **#92** — 5 apps built & launched in a single day (LinkedIn).
- **#102** — Agent ships micro-apps via *Val Town* (Discord).
- **#104** — Long-running instance accumulates codebase knowledge (commit style, legacy API sequences) (Medium).
- **#106** — **Recall**: Hermes-native inspectable (non-black-box) memory provider (Discord).
- **#109** — Non-coder had *Codex* build a full VPN service (Xray/Wireguard, admin panel) (Discord).
- **#116** — Local *Gitea + watchtower* auto-restarts Hermes within 10 min of push (Discord).
- **#119** — Memory kernel that "compiles thoughts, not vectors"; auto contradiction resolution (Discord).
- **#126** — Hermes more stable; used to troubleshoot **OpenClaw** (Reddit).
- **#127** — Native SwiftUI Mac app (SSH to host/files); **Hermes Desktop v0.4.0** (Discord).
- **#128** — Session-compression plugin preserving work thread; **Hermes Operational Checkpoint** (Discord).
- **#136** — Persistent structured memory because compression drops constraints after ~30 turns; **Hermes Memory** (Discord).
- **#140** — Compile skills into code, invoke AI only at needed steps (model-agnostic reliability) (Discord).
- **#143** — Built-in **Kanban multi-agent**: parent posts cards → child sub-agents pull → parallel → report; "GAME CHANGING" (Reddit).
- **#149** — Vectorless RAG via *PageIndex* + tool-based reasoning (Discord).
- **#156** — Custom TUI so Hermes "feels like OpenCode"; project **Herm** (Discord).
- **#158** — **Rookery**: local llama-server process manager (Discord).
- **#160** — UI for the memory system they loved after trying *Mem0/QMD/Mempalace/Honcho*; **OpenConcho** (Discord).
- **#162** — Telegram → *Modal* serverless; *~40% faster* on research vs fresh agent; TokenMix benchmark (Medium).
- **#163** — "Every OpenClaw update breaks something — Hermes just runs" (Reddit).
- **#173** — Hermes in *NixOS + container* (Discord).
- **#178** — **Dream Auto**: idle *MCTS background reasoning*, injects insights into context (Discord).
- **#181** — Hermes as a **watchdog** over OpenClaw; "saves countless hours and credits" (X).
- **#184** — Competitor-analysis swarm ported from *Codex* in ~2h; custom memory routing (Discord).
- **#187** — "Hermes is OpenClaw set up + 1 week debug + RAG + memory + better tool calling"; *Qwen3.5-9b on 16GB VRAM, 10/10* (Reddit).
- **#190** — **STANDING.md** plugin injects standing instructions via `pre_llm_call` so the agent stops guessing (Discord).
- **#197** — `SKILL.md` as a Notion/Outlook/SharePoint tool router (Discord).
- **#198** — 3,000+ self-improvement logs on a custom NL harness; *MiniMax m2.7* (Discord).
- **#207** — **Skill Factory**: silently watches workflows → writes `SKILL.md` + `plugin.py` (GitHub).
- **#213** — 8h/day email pipeline; *DBOS + PostgreSQL + S3 + Gmail API + Claude Opus*, 3-actor (GitHub).
- **#217** — Hermes orchestrates *Claude Code/Codex over SSH*; Hermes writes prompts & reviews (Discord).
- **#218** — All llama.cpp run/optimize knowledge packaged as a skill (Discord).
- *(plus further Dev Workflow entries among the ~42 uncaptured tail).*

### Personal Assistant — Trợ lý cá nhân (chủ động, bộ nhớ, gia đình, sức khỏe)

- **#1** — "Every weekday 9am summarize inbox → Slack"; NL cron; writes its own skills (Blog).
- **#2** — Self-hosted Google Drive via *Nextcloud + LibreOffice* (Discord).
- **#5** — "Google me and ship a landing page to my VPS"; search → build → *SSH* deploy → text me (X).
- **#19** — Google Tasks create/update/list (GitHub).
- **#21** — Bedtime stories with consistent protagonist across sessions (GitHub).
- **#22** — Daily Obsidian journaling; testing *Kimi 2.5* (OSS models weaker at skill-triggering vs Sonnet 4.6) (Discord).
- **#33** — Raspberry Pi 5 running Hermes 24/7; memory not synced across devices (Discord).
- **#36** — Tasks across *Obsidian + Apple Calendar + Signal*, Turkish, cron (Discord).
- **#38** — "Claude (Opus 4.7) for chat, Hermes 24/7 on a mini PC for real-world stuff" (email, forms, calendar) (Discord).
- **#44** — Two-tier email: Python detects, LLM fires only when needed; *himalaya IMAP* (Discord).
- **#60** — Pi 4 home-server "central brain"; persistent memory (GitHub).
- **#62** — *Qwen3.5:4b on a 5060Ti*; Telegram assistant; 4B "snappy, alive" (Reddit).
- **#63** — Discord assistant on *GPT-5.5 / DeepSeek v4*; "life changing" (X).
- **#72** — Voice-first fitness coach learning body patterns (training→nutrition→recovery); Telegram (Discord).
- **#73** — Meal planner; **Meal Manager** plugin; weighted score *60% availability/40% recency* (Discord).
- **#82** — Apple Health + Threads + Gmail + Calendar in one CLI; "Hermes = CEO, OpenClaw = Senior Engineer" both on Obsidian (Substack).
- **#95** — 3-layer memory doctrine: durable facts / session search / skills; "save facts, not task progress" (Discord).
- **#96** — "9am check HN → DM Telegram"; MEMORY.md + USER.md, FTS5 search, NL cron (dev.to).
- **#101** — Proactive check-ins ("anything to watch this afternoon?") (GitHub).
- **#105** — "5 things Hermes does ChatGPT won't": persistent memory, runs code, acts in apps, messages first (Reddit).
- **#107** — **Obsidian vault as long-term memory backbone**; 794 upvotes (Reddit).
- **#115** — Personal assistant on *Qwen3.5 27B* (VERY good) (Reddit).
- **#122** — Semantic knowledge substrate over Obsidian/vimwiki/Hermes sessions; **Cartographer + mapsOS** (Discord).
- **#124** — Cron nudges via Discord/Signal for executive function; ~14k tokens (Discord).
- **#135** — One Hermes for a family of 3 on WhatsApp; replaced a $200 ChatGPT sub (X).
- **#147** — Hermes over iMessage on always-on Mac Studio; in group chats (GitHub).
- **#174** — Reads HackerNews → daily email summary (Discord).
- **#176** — Obsidian + home automation + server mgmt on a cheap locked-down VPS (HN).
- **#179** — PM agent runs morning/evening standups for ADHD; Manager + Paperclip sub-agents (X).
- **#180** — Memory lets user jump between projects (vs OpenClaw "one-track"); + Paperclip (Reddit).
- **#188** — iOS sensors (health/location/voice) → contextual answers (Discord).
- **#195/#205** — Health Connect / Whoop biometric data pulled in locally (Discord).
- **#202** — "Replaced everything with a single Hermes"; autoresearch + LLM-wiki second brain (X).
- *(#45, #55/#56 mapsOS, others).*

### Integrations — Tích hợp (MCP, connector, phần cứng, thương mại)

- **#4** — *Hindsight Cloud* memory connector; *Vectorize.io* (LinkedIn).
- **#14** — Team agent: *SourceDev* repo index, *Tenderly MCP* onchain debug, LLM-Wiki (Discord).
- **#15** — Hermes + Browser Harness on Hostinger VPS; *claude-opus-4.7 via OpenRouter* (Gist).
- **#17** — **jMunch MCP**: *52 tools* via tree-sitter for code intelligence (GitHub).
- **#47** — **Vercel Sandbox** backend (microVMs, snapshot FS); backends now local/Docker/Modal/SSH/Daytona/Singularity/Vercel (GitHub PR).
- **#48** — Full *Feishu (Lark)* coverage (Docs/Sheets/Bitable/Calendar/Wiki/Drive/Email) (GitHub).
- **#93** — *Firecrawl* scrape/search/browse (LinkedIn).
- **#97** — **Onchain identity + proof-of-work** attestations via *Ethereum Attestation Service* on Base mainnet (Discord).
- **#103** — *Hunter.io* email lookup via *Composio MCP* for sales (GitHub).
- **#108** — `hermes mcp serve`: "fat agent → thin tool provider"; exposes 15+ platforms, FTS5, 73-skill surface (Gist).
- **#117** — AdGuard Home plugin (Discord).
- **#118** — Themed browser **Webchat** UI on MEMORY.md + USER.md (GitHub).
- **#132** — Watches homelab validators (*0G, FortyTwo*), pings Telegram on state change; ex-OpenClaw (Discord).
- **#137** — Cross-agent memory across *Hermes + Claude Code + Cursor*; BM25 + vector + KG (GitHub).
- **#138** — Remote-start car via *OnStar* skill (Discord).
- **#139** — Desktop computer-use module (noVNC, screenshots, mouse/keyboard) (GitHub).
- **#142** — *JMAP* email for Fastmail (GitHub).
- **#151** — Discord-read plugin (missed from OpenClaw) (Discord).
- **#159** — Bundled many API keys into single endpoints (finance via one call) (Discord).
- **#170** — *BoltAI v2* gateway plugin (markdown + slash commands) (Discord).
- **#186/#209** — **Home Assistant** add-on; "zero to agent in under 5 min" (Discord/X).
- **#189** — **agentbox.id**: agent-optimized email service (Discord).
- **#200** — *M5 Cardputer* embedded device via API (OTA, TTS/STT) (Discord).
- **#204** — **Merxex**: agent-to-agent **commerce**/monetization layer (buy/sell services) (GitHub).
- **#214** — Agent's own inbox via *AgentMail MCP* (no SMTP/OAuth) (X).
- **#219** — Hermes in *Zed* via **ACP Registry** (auto discovery/install) (GitHub).

### Meta & Ecosystem — Meta & Hệ sinh thái (dashboard, installer, bản đồ hệ sinh thái, hosting)

- **#20** — **hermes-for-win**: one-click Windows installer, auto-start (GitHub).
- **#24** — Podcast: "Hermes has won" — self-improving skills, 3-layer memory (Spotify).
- **#28** — TUI dashboard watching the agent think; **Hermes HUD** (Discord).
- **#49** — Show HN independent install guide (macOS/Linux/WSL2/Termux) (HN).
- **#80** — Browser dashboard: PTY terminal, file editor, gateway control, token analytics (Discord).
- **#94** — Every tool call → per-profile SQLite + *5 Grafana dashboards* (Discord).
- **#111** — "One month with Hermes: don't build the whole machine on day one" (Reddit).
- **#145** — "Switched from OpenClaw to Hermes, not looking back" (X).
- **#146** — **awesome-hermes-agent**; tied to **agentskills.io** standard (GitHub).
- **#153** — 4 custom skins for HermelinChat GUI (Discord).
- **#164** — Product Hunt: competitor (Clawdi) calls it "the best self-improving agent we've used" (Product Hunt).
- **#167** — Native Windows app wrapper (Reddit).
- **#171** — macOS control center for local models on 2 machines (Discord).
- **#182** — **H-OPS**: operator dashboard for multi-agent on Hermes Kanban (Discord).
- **#183** — **hermesatlas.com**: scraped whole ecosystem, star-rated by category (X).
- **#192** — Mini-documentary with hackathon finalists + Nous co-founder (Discord).
- **#196** — 4 agents (PM/Dev/Ops/Content) 24/7 on 32GB Ubuntu; *5 MCP servers, 34 tools*, daily auto-distillation (Discord).
- **#201** — **Hermify**: managed hosting (bring API key + Telegram bot) (Reddit).
- **#211** — Shadow-to-live **migration path from OpenClaw** (GitHub).

### Business Ops — Vận hành kinh doanh

- **#42** — Roofing lead-gen/CRM app (Discord).
- **#59** — 24/7 assistant on *Supabase CRM*; agent proposed a "Supabase MCP scripts" skill itself (YouTube).
- **#100** — Triages & works tickets in *Plane.so*; documents to Obsidian; + Claude Code (Discord).
- **#103** — Hunter.io sales outreach (see Integrations).
- **#113** — Create/edit *Google Slides* decks (GitHub).
- **#125** — "Day 297 streak: **$100K of client work automated**"; 900k+ compute-sec, 5B+ tokens (X).
- **#134** — **Hermes as Chief of Staff**: main agent w/ cross-project memory + per-project sub-agents (one per Slack channel); daily WhatsApp report (Discord).
- **#168** — Task-centric memory for a printing factory; auto-categorize (Printing/Stocks), compress done tasks to cards (GitHub).
- **#184** — Competitor-analysis swarm (see Dev Workflow).
- **#194** — Auto-transcribe Meet, control from Teams, local models for client data (Substack).

### Cost Optimization — Tối ưu chi phí

- **#27** — Switch Hermes/OpenClaw with free models on *primeclaws.com* (Reddit).
- **#30** — Replaced Perplexity (~$10/few days) with **Gigaxity** (7 MCPs + SearXNG) (Discord).
- **#61** — Multi-agent, weeks continuous, Telegram; "what I use it for & how I keep it cheap" (X).
- **#70** — **ZeroID**: *RFC 8693 token exchange* for sub-agent scope delegation & context cost (Discord).
- **#99** — **90% token cut** (~$130/5d → ~$10/5d); Android via *Termux + OpenRouter*; "customization is a trap; output is the skill" (Podcast).
- **#112** — Under **$20/mo** (Minimax M2.7 VPS) vs OpenClaw Mac-Mini-M4 + Opus 4.6 ~$80–150/mo (Medium).
- **#114** — Free GPT-4.1 via Copilot Pro ($10/mo); Hermes delegates coding to *OpenCode* (Discord).
- **#148** — $10/mo Hetzner VPS; *Claude Opus via OpenRouter* (YouTube).
- **#165** — Smart-routing tiers (*Gemini 3.1 Flash Lite* mechanical / *Sonnet* delicate / *Minimax* low-overhead); saved ~10h + $40 (Reddit).
- **#177** — Multi-agent on *Ollama* to cut cost (Discord).
- **#185** — **RTK** integration rewrites terminal commands; **60–90% context-token cut** (Discord).

### Content Creation / Marketing — Sáng tạo nội dung / Marketing

- **#6** — Turkish locale skill pack (TRY data, Turkish news, daily PNG cards, Telegram cron); zero API keys (Discord).
- **#8** — Skill pack on *Meta CLI/MCP* (Marketing) (Discord).
- **#16** — Weekly cron: top-3 trending AI tools → makes a reusable skill (YouTube).
- **#40** — **UGC ad studio**: URL → scrape → *Meta Ads Library + TikTok Creative Center* hooks → brief in *~4 min*, zero prompt-eng; *Higgsfield* (X).
- **#77** — X roast-poster without a $100 API sub (Discord).
- **#123** — Writes in the user's voice (reads their articles first); Mac Mini running OpenClaw + Hermes (X).
- **#129** — Cron triages tech news into Discord channels by urgency, 3×/day (X).
- **#141** — Tweets in the creator's voice from past scripts; recalls preferred emojis in a new session (YouTube).
- **#155** — LinkedIn posts that remember the user's style (YouTube).

### Research — Nghiên cứu

- **#30** — Custom research stack (see Cost).
- **#65** — Daily research brief → Discord/Slack/Notion/Obsidian/email; tracks ignored items, self-improves (X).
- **#66** — **Hermes-lab**: autonomous experiment bookkeeper (Karpathy/Sakana/AIDE-inspired) (Discord).
- **#76** — Ported the Python weather stack (MetPy/Herbie/cfgrib/WRF) to Rust for plugins (Discord).
- **#79** — Self-improving **LLM Wiki second brain** (Karpathy pattern); public site (Medium).
- **#206** — **AI-assisted drug discovery for Africa** (pharmacy undergrad); *ChEMBL, AlphaFold, OpenFDA, QSAR* (Discord).

### Enterprise — Doanh nghiệp

- **#32** — Daily cybersec+AI briefing on a **local k8s cluster** (Discord).
- **#37** — Native **Vertex AI** provider for GCP-standard orgs (GitHub).
- **#54** — **EU AI Act compliance via Ombre**: tamper-proof audit, prompt-injection blocking, memory encryption, hallucination detection, cost tracking, compliance exports (GitHub).
- **#90** — Azure-compliant prompt patch to avoid content-filter trips (Gist).
- **#130** — CLI/gateway-first: **13 messaging platforms under one process** (Substack).
- **#175** — AWS VPS + Google Workspace automation; setup non-trivial (Discord).
- **#193** — "95% of AI users see no results" VC deep-dive on Hermes swarms/experiment loops (X).
- **#208** — **Kubernetes pod-hop handoff** across restarts on shared PVC (GitHub).

### Trading & Markets — Giao dịch & Thị trường

- **#7** — Self-learning weather-trading bot: scans every 60 min, compares 3 forecasts, buys undervalued buckets; **$100 → $216 in 48h** (X).
- **#81** — Polymarket: reads 4 layers in parallel (order book, on-chain addresses, news-lag, positions); *Polymarket module + News Skill* (X).

### Messaging — Nhắn tin

- **#23** — **QQ Bot** adapter for China (822 lines; 95M+ users) (GitHub).
- **#26** — DM-based **approval gate** for kid-facing Discord bots (GitHub).
- **#34** — **LINE** integration ask (95M+ MAU Japan) (GitHub).
- **#157** — Native Android client **Hermes Relay** (streaming, slash cmds, tool viz) (Discord).
- **#191/#203/#210** — Remote phone control / home-server + Telegram / web-proxy session hand-off to mobile (Discord).

### Privacy & Self-Hosted — Riêng tư & Tự lưu trữ

- **#9** — Shared local *SearXNG* container across agents (Discord).
- **#85** — **Legal work on an edge GPU, 4B Gemma, no cloud APIs**; "self-hosting the main loop is non-negotiable" (GitHub).
- **#91** — "Sandbox it — don't give it free reign" (HN).
- **#131** — *Tailscale serve* — secure remote access, no exposed ports (GitHub).
- **#154** — Independent security eval: 5 defensive patterns (OSV malware check for MCP packages, credential stripping) (Gist).
- **#212** — Skill that hardens the agent against common LLM threats (Discord).

### Creative — Sáng tạo

- **#35** TouchDesigner generative visuals · **#39** B1 droid skin · **#46** X→NotebookLM podcast workflow (agent-designed) · **#71** chess blunder finder (blunder-lens.com) · **#87** agent "dreams" nightly, 5 REM cycles 23:00–06:00, **~$0.014/night on Haiku** · **#89** shadcn finance dashboard + Manim explainers · **#98** Matrix skin · **#110** auto-play Minecraft skill (20+ min thinking) · **#120** **Hermes Inc.** Telegram startup-sim (AI teammates argue/remember/evolve) · **#121** personal web-dev style as a skill · **#133** speech-to-speech + generated ambient music · **#150** agent tone examples co-written with a sibling (agent "Reina") · **#166** long voice-call timeout plugin · **#169** twice-daily Tidal curation · **#192** documentary · **#215** spare-laptop Hermes autonomously builds a **RenPy visual novel (10 images) in ~10 min** via LM Studio + ComfyUI · **#216** browser translate/summarize extension (Hermes-4-70B).

### General — Tổng quát

- **#11** Voice-from-terminal for accessibility (*Whisper.cpp*) · **#68** "AI employee for my hardest tasks" (Hermes + ChatGPT 5.5) · **#144** local community agent on a 16GB Mac mini · **#152** teaching a Linux user group to build agents · **#161** **blind-since-birth user built an NVDA screen-reader translator addon** · **#199** Spanish Hermes guide built with Hermes.

*(#220 và khoảng ~41 story khác ở phần đuôi của trang đã bị cắt bởi giới hạn độ dài nội dung khi lấy về;
220 story đã ghi lại vẫn bao phủ toàn bộ 15 danh mục và mọi mẫu hình tích hợp được nêu tên.)*

---

## B. Các con số cứng, được rút ra

| Chỉ số | Story |
|---|---|
| **$100 → $216 trong 48h** (bot giao dịch theo thời tiết) | #7 |
| **$100K công việc khách hàng được tự động hóa** (chuỗi ngày thứ 297) | #125 |
| **Cắt 90% chi phí token** ($130/5 ngày → $10/5 ngày, Android/Termux) | #99 |
| **Cắt 60–90% token ngữ cảnh** (RTK) | #185 |
| **73% mỗi lệnh gọi API là chi phí cố định** (đo được) | #13 |
| **$0.014/đêm** cho các chu kỳ "dream" trên Haiku | #87 |
| **Dưới $20/tháng** cho toàn bộ thiết lập (Minimax VPS) so với OpenClaw $80–150/tháng | #112 |
| **Nhanh hơn ~40%** khi nghiên cứu so với một agent mới toanh | #162 |
| **112/129 phiên** vi phạm cổng phê duyệt (kiểm toán) | #51 |
| **12 phiên Hermes song song/ngày; 5B+ token; repo GitHub top-100** | #43 |
| **794 upvote** cho mẫu hình Obsidian-làm-bộ-nhớ | #107 |
| **52 công cụ** (jMunch MCP) · bề mặt **73 skill** (mcp serve) | #17, #108 |

---

## C. Những story GẦN NHẤT với luận điểm Twin Terminal (phần đối chiếu)

Đây là những story cần nghiên cứu — mỗi story là một mảnh của cỗ máy mà bạn dự định sở hữu.
Xem tài liệu tầm nhìn Twin Terminal (02) để biết các con số của engine.

**Mã hóa nghề/giọng của một người cụ thể được nêu tên (Engine 02 + 06 — chính là "twin").**
Đây là cụm đúng-luận-điểm nhất: người dùng Hermes *đã và đang nhân bản phán đoán và
giọng của từng cá nhân.*
- **#123** viết theo giọng của người dùng (đọc bài viết của họ trước); **#141/#155**
  học và *ghi nhớ* phong cách + emoji của một nhà sáng tạo qua nhiều phiên; **#150** cùng
  viết giọng của agent với một người thân. → Đây là một **twin chuyên gia cấp tiêu dùng**
  với **không có kiểm định độ trung thành**. Cổng của bạn chính là lớp còn thiếu đó.
- **#82** "Hermes = CEO, OpenClaw = Kỹ sư cấp cao" — các twin chuyên biệt theo vai trò đã
  đang cộng tác với nhau.

**Nắm bắt chuyên môn khan hiếm/vận hành (luận điểm về nguồn cung của bạn).**
- **#168** bộ nhớ tác vụ của nhà máy in; **#14** kiến thức giao thức nhóm/onchain;
  **#206** quy trình khám phá thuốc; **#218** "toàn bộ kiến thức chạy mô hình của tôi
  đóng gói thành một skill." → Cùng bản năng với **Cloneable** (xem tài liệu bối cảnh
  startup, 05): biến nghề ngầm định thành các agent tái sử dụng được. Không có story nào
  kiểm định độ trung thành.

**Hội đồng đa agent / điều phối (Engine 04).**
- **#134** Chief-of-Staff + các sub-agent theo dự án; **#143/#78** Kanban fan-out cha→con;
  **#88** pipeline lập kế hoạch→code→QA→ship; **#181** agent watchdog; **#196** tổ chức 4
  agent với auto-distillation. → "Các hội đồng twin xuyên tổ chức / bản đồ bất đồng" của
  bạn đã có tiền lệ đang hoạt động ở phạm vi đơn tổ chức tại đây.

**Xác minh, phê duyệt, kiểm toán, trôi dạt (Engine 07 — các láng giềng của lợi thế phòng thủ).**
- **#12/#26** các cổng phê duyệt; **#51** *kiểm toán phiên đo được 112/129 lần vi phạm
  cổng*; **#54** Ombre tuân thủ EU-AI-Act (kiểm toán chống giả mạo, phát hiện ảo giác);
  **#190** STANDING.md để agent ngừng đoán mò; **#154** đánh giá bảo mật.
  → Hệ sinh thái đang *với tới các nguyên hàm về niềm tin/kiểm toán* nhưng **không ai đo
  độ trung thành so với một chuyên gia được nêu tên cụ thể**. Đây là xác nhận rõ ràng
  nhất rằng Engine 07 là khoảng trống thị trường mở **ngay cả bên trong cộng đồng Hermes.**

**Bộ nhớ như một tài sản bền vững, có thể kiểm tra (Engine 02).**
- **#31/#53/#75/#86/#106/#119/#136/#160** — cả một ngành thủ công gồm các memory kernel
  (đồ thị SQLite, phân rã theo thời gian, giải quyết mâu thuẫn, "có thể kiểm tra, không
  phải hộp đen"). → "Sổ nghề đi theo twin" của bạn là *cùng một nhu cầu*, ở một tầng cao
  hơn. Nhiều khả năng bạn có thể **áp dụng/hợp tác** thay vì xây dựng bộ nhớ từ đầu.

**Thương mại / danh tính / chứng thực (Engine 05 & 09).**
- **#204 Merxex** = lớp **thương mại** agent-với-agent; **#97** = **danh tính onchain +
  chứng thực proof-of-work** (Ethereum Attestation Service). → Các phiên bản sơ khai của
  engine danh tính + royalty của bạn đã tồn tại để xây dựng lên trên hoặc học hỏi. Chứng
  thực on-chain là một cơ chế ứng viên cho xuất xứ/huy hiệu có bằng chứng chống giả mạo.

**Tự động sinh skill & một chuẩn skill (Engine 06 + kệ registry).**
- **#207 Skill Factory** và **#67** skill-audit tự cải thiện; chuẩn **#146 agentskills.io**
  + kệ registry/xếp hạng **#183 Hermes Atlas**. → "Xưởng lấp đầy kệ" và một **chuẩn skill
  + registry có xếp hạng đã tồn tại** trong thế giới Hermes. Registry của bạn nên
  **tương tác với agentskills.io**, chứ không phát minh lại nó — và bổ sung thứ duy nhất
  mà xếp hạng sao của Atlas thiếu: *độ trung thành đo được + hạn dùng.*

## D. Hermes và OpenClaw được triển khai cùng nhau

OpenClaw liên tục xuất hiện như điểm tham chiếu của Hermes — thường **chạy song song**:
- **#82** Hermes(CEO)+OpenClaw(Kỹ sư cấp cao) trên một Obsidian vault; **#123** cả hai
  trên một Mac Mini; **#181** Hermes làm *watchdog trên OpenClaw*; **#126** Hermes xử lý
  sự cố cho OpenClaw; **#132/#151/#180** người dùng chuyển từ OpenClaw nhưng port lại các
  tính năng của nó.
- **Di trú/chuyển đổi:** **#27** primeclaws.com chuyển đổi giữa hai bên; **#145**
  "đã chuyển, không nhìn lại"; **#187** "Hermes là OpenClaw + 1 tuần debug + RAG +
  bộ nhớ"; **#163** "mỗi bản cập nhật OpenClaw đều làm hỏng thứ gì đó"; **#211** công cụ
  di trú shadow-to-live; **#211/#151** các bản port ngang bằng tính năng.

**Hàm ý:** hai hệ sinh thái này **tương tác được và kề cận nhau**, và người dùng đã chạy
các thiết lập đa runtime. Một nền tảng **độc lập runtime ở tầng twin** (kiểm định/host
twin bất kể runtime bên dưới là Hermes hay OpenClaw) là một định vị đáng tin — nó cưỡi
trên thực tế rằng người dùng đã trộn lẫn chúng. Những startup tận dụng *cả hai* hiện nay
hầu hết là **những power user cá nhân và công cụ nhỏ** (primeclaws.com, các thiết lập
watchdog, tiện ích di trú), chứ không phải các công ty được cấp vốn — tức là ô "twin-trên-
nhiều-runtime" **vẫn còn để trống.**

## E. Danh mục này có ý nghĩa gì với Brain4All

1. **Nhu cầu đã được chứng minh và rộng khắp.** 262 story thật, có nguồn, trải khắp 15
   lĩnh vực, phần lớn từ cá nhân và nhóm nhỏ — thị trường *agent cá nhân/chuyên gia tự lưu
   trữ* đang sống, không phải suy đoán.
2. **Mọi engine hàng hóa đều đã bão hòa** bởi cộng đồng (memory kernel, dashboard,
   connector, routing, sandbox). **Đừng xây những thứ này** — hãy áp dụng, hợp tác, hoặc
   cưỡi theo chuẩn (agentskills.io, Atlas, Merxex, EAS). Xác nhận các tài liệu công nghệ
   lõi & đối thủ (03) và lộ trình (08): thuê engine hàng hóa.
3. **Lợi thế phòng thủ được xác nhận là trống ngay từ bên trong.** Cộng đồng đang *tích
   cực xây các nguyên hàm kiểm toán, phê duyệt, tuân thủ và niềm tin* (#51, #54, #154,
   #190) — nhưng **không một ai đo liệu một twin có tái tạo trung thành một con người được
   nêu tên hay không.** Engine 07 là khoảng trống thị trường ngay cả giữa những người gần
   công nghệ nhất.
4. **Nhân bản giọng/phán đoán cá nhân đã là use case sát thủ** (#123, #141, #155, #150) —
   nó chỉ thiếu kiểm định, xuất xứ và royalty. Đó chính xác là mũi nhọn của Twin Terminal,
   và điều đó nghĩa là **bạn đang mở rộng một hành vi đã được chứng minh, chứ không phát
   minh ra nhu cầu.**
5. **Tương tác, đừng phát minh lại registry.** agentskills.io + Hermes Atlas là những kẻ
   dẫn đầu hiện hữu của "cái kệ." Sự khác biệt của bạn là **độ trung thành đo được +
   hạn dùng + lineage royalty** xếp chồng lên trên (hoặc bắc cầu tới) chuẩn đó.
6. **Nous đã kiếm tiền từ hosting (Hermify).** Củng cố tài liệu công nghệ lõi & đối thủ
   (03): đừng cạnh tranh về hosting; hãy cạnh tranh về kiểm định.

