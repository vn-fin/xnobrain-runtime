# 03 · Công nghệ cốt lõi & Đối thủ trực tiếp

> Nền tảng bạn đang xây dựng trên đó (Hermes + một bộ định tuyến), những gì nó làm
> được và không làm được, các hàm ý về giấy phép của nó, và những runtime mã nguồn mở
> mà bạn sẽ bị đem ra so sánh. Mọi tuyên bố từ nguồn bên ngoài đều có liên kết và ghi ngày.

## 1. Hermes Agent (Nous Research) — động cơ của bạn

**Nó là gì.** Một AI agent tự cải thiện, mã nguồn mở, **giấy phép MIT**, ra mắt
**tháng 2 năm 2026**. Đây chính xác là thứ mà XNOBrain bọc lại (chính là "ứng dụng
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
  công cụ) — đây chính là lõi phê duyệt Hermes mà XNOBrain giữ lại.

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
Hệ quả cho XNOBrain:

- ✅ Bạn có thể hợp pháp xây dựng một **tầng doanh nghiệp/đám mây độc quyền** lên trên
  Hermes và giữ tầng đó đóng. Đây chính xác là điều mà mô hình chia tách open-core giả định.
- ✅ Bạn **không** bị buộc phải mở mã nguồn của chính mình (khác với AGPL/GPL).
- ⚠️ Mặt trái: MIT cũng không cho *bạn* sự bảo vệ nào — bất kỳ ai (kể cả một cloud
  hyperscaler hay chính Nous) đều có thể bọc Hermes. **Lợi thế phòng thủ (moat) của bạn
  không thể là "chúng tôi đã bọc Hermes."** Nó phải là cổng kiểm định, dữ liệu nghề,
  danh tính, và lineage royalty (các động cơ 05–07, 09). Điều này nhất quán với tài
  liệu tầm nhìn.
- 📌 **Hành động:** giữ một tệp ghi công/NOTICE sạch cho Hermes và bộ định tuyến, cùng
  một bảng kiểm kê giấy phép của các phụ thuộc, trước khi phân phối công khai. Repo OSS
  của chính bạn vẫn cần chọn một giấy phép — README nói thẳng *"Chọn và thêm một giấy
  phép trước khi phân phối công khai."* Xem tài liệu về mô hình kinh doanh và cấp phép.

## 2. Bộ định tuyến ("9router") và bức tranh định tuyến

"9router" của XNOBrain là một bộ định tuyến LLM (một tiến trình chạy trên `:20128`)
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

**Nó trùng lặp và khác biệt với Hermes/XNOBrain như thế nào:**

| | Hermes / XNOBrain | OpenClaw |
|---|---|---|
| Mô hình cấu hình | Skill + hồ sơ + UI | `SOUL.md` ưu tiên cấu hình |
| Bộ nhớ | MEMORY.md + FTS5 + skill | markdown + SQLite |
| Phân phối | nhắn tin + không gian làm việc web | gateway nhắn tin |
| Tự cải thiện | ✅ tự viết skill của mình | hạn chế |
| **Kiểm định / độ trung thành của twin** | lợi thế phòng thủ bạn dự kiến | ❌ không có |

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
   câu nói phân biệt XNOBrain với toàn bộ danh sách này.
