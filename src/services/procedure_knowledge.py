"""Application facts, separate from building policy and public law.

This catalog describes implemented screens and validation, not a building's
verified requirements. It works independently of document ingestion.
"""

import re
import unicodedata


def fold_text(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold()).replace("đ", "d")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\bdang ki\b", "dang ky", text)


def contains_phrase(text: str, phrase: str) -> bool:
    """Match complete words in folded text, including spaced/hyphenated phrases."""
    words = phrase.split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(word) for word in words) + r"(?!\w)"
    return bool(re.search(pattern, text))


# Grounded in web/app.js residentEntryForms and authenticated workflow APIs.
GUIDES = {
    "account_registration": {
        "title": "Đăng ký tài khoản cư dân phụ thuộc", "form": "account_registration",
        "aliases": ("tai khoan cu dan phu thuoc", "tai khoan ung dung", "cap tai khoan", "dang ky tai khoan", "dependent account"),
        "steps": ("Chỉ chủ sở hữu đăng ký cho người thuê hoặc thành viên gia đình đã được BQL duyệt cư trú và chưa có tài khoản.",
                  "Mở Đăng ký tài khoản cư dân phụ thuộc, chọn người trong danh sách, xem lại và xác nhận gửi BQL.",
                  "BQL đối chiếu hồ sơ và cấp tài khoản ngẫu nhiên. Mở ticket hoặc nhóm thông báo, chọn Xem tài khoản, mật khẩu để bàn giao riêng.",
                  "Cư dân phụ thuộc phải đổi mật khẩu ngay lần đăng nhập đầu. Sau đó không thể đọc lại mật khẩu tạm; không chia sẻ mật khẩu qua chat."),
    },
    "profile": {
        "title": "Cập nhật thông tin cư dân", "form": "profile",
        "aliases": ("thong tin cu dan", "ho so cu dan", "resident profile"),
        "steps": ("Mở thông tin cư dân, kiểm tra họ tên, tư cách, khu, tòa và căn hộ BQL đã cấp; những thông tin này chỉ được xem.",
                  "Chỉ cập nhật nhu cầu sinh hoạt, xem lại rồi xác nhận lưu. Nếu danh tính hoặc căn hộ sai, liên hệ BQL đối chiếu, không tự sửa.",
                  "Checklist sẽ phản ánh hồ sơ mới; số người ban đầu chưa tính các hồ sơ tạm trú đã được BQL duyệt."),
    },
    "verify_residency": {
        "title": "Xác nhận thông tin cư trú", "form": "verify_residency",
        "aliases": ("ho so cu tru", "thong tin cu tru", "xac nhan cu tru", "verify residency"),
        "steps": ("Hoàn thành đầu tiên: chủ sở hữu xác thực quyền sở hữu căn hộ; người thuê/người nhà xác nhận thông tin cư trú.",
                  "Kiểm tra thông tin BQL đã cấp, chỉ xem và xác nhận, không tự sửa danh tính hoặc căn hộ. Nếu sai, liên hệ BQL trước khi xác nhận.",
                  "Xem lại, đồng ý rồi xác nhận lưu để mở các thủ tục khác. Đây không phải chứng nhận quyền sở hữu pháp lý."),
    },
    "temporary_residency": {
        "title": "Thêm người cư trú / đăng ký tạm trú", "form": "temporary_residency",
        "aliases": ("tam tru", "o tam", "o nho", "o vai ngay", "nguoi o cung", "nguoi cung o", "them nguoi", "them thanh vien", "o qua dem", "temporary residence", "temporary residency"),
        "steps": ("Mở Thêm người cư trú, nhập họ tên và số CCCD 12 chữ số; bổ sung ngày bắt đầu/quan hệ nếu có.",
                  "Hoặc kể nhu cầu ở tạm, ở nhờ hay ở cùng trong chat. Trợ lý giữ cùng bản nháp khi bạn bổ sung tên, CCCD và ngày bắt đầu qua nhiều tin nhắn; xem lại rồi xác nhận mới gửi BQL.",
                  "Xem lại và xác nhận gửi. BQL có thể yêu cầu bổ sung trước khi duyệt.",
                  "Nếu chưa thêm người ở cùng, chủ sở hữu có thể chọn Chưa có nhu cầu rồi xem lại và xác nhận. Chỉ hoàn thành checklist, không tạo hay gửi ticket; vẫn đăng ký được sau này.",
                  "Xem Ticket đã gửi và Quản lý căn hộ. Gửi ticket này không thay việc đăng ký tạm trú với cơ quan nhà nước."),
    },
    "vehicle_registration": {
        "title": "Đăng ký thẻ xe / phương tiện", "form": "vehicle_registration",
        "aliases": ("xe may", "o to", "xe hoi", "the xe", "dang ky xe", "gui xe", "phuong tien", "bien so", "vehicle", "vehicles", "parking", "motorbike", "motorbikes", "car", "cars"),
        "steps": ("Mở Đăng ký thẻ xe; chọn xe máy hoặc ô tô và nhập biển số.",
                  "Đính kèm ảnh giấy đăng ký xe JPG/PNG/WebP tối đa 5 MB. Ảnh bắt buộc cả khi nhập tay; mỗi xe một hồ sơ và ảnh riêng.",
                  "Có thể đồng ý đọc ảnh tự động hoặc nhập tay nếu ảnh mờ. Kiểm tra biển số/loại xe trước khi xác nhận gửi BQL.",
                  "Chưa cần đăng ký xe: chọn Chưa có nhu cầu, xem lại và xác nhận để hoàn thành checklist; không cần ảnh vì không tạo hay gửi ticket. Nếu đăng ký thật thì ảnh vẫn bắt buộc.",
                  "Theo dõi Ticket đã gửi. BQL đối chiếu ảnh và duyệt; đã duyệt chưa đồng nghĩa đã hoàn tất cấp thẻ."),
    },
    "resident_card": {
        "title": "Đăng ký thẻ cư dân", "form": "resident_card",
        "aliases": ("the cu dan", "the dan cu", "the ra vao", "resident card", "dang ky the", "lich nhan the", "ngay gio nhan the"),
        "steps": ("Kiểm tra thông tin cư dân và căn hộ, mở Đăng ký thẻ cư dân.",
                  "Có thể chọn ngày và giờ mong muốn nhận thẻ; nếu chọn lịch, điền đủ cả hai. Chọn giờ 00–23 và phút 00–59, không dùng AM/PM. Nếu chưa xác định lịch, để trống cả hai.",
                  "Không cần Điền nhanh từ giấy tờ trong form này. Bổ sung ghi chú nếu cần, xem lại và xác nhận gửi BQL. Ngày giờ được lưu trên cùng ticket để BQL xem xét; đây chưa phải lịch nhận đã xác nhận.",
                  "Theo dõi Ticket đã gửi; bổ sung giấy tờ nếu BQL yêu cầu. Chỉ trạng thái hoàn tất mới xác nhận xử lý xong."),
    },
    "card_replacement": {
        "title": "Báo mất / cấp lại thẻ", "form": "card_replacement",
        "aliases": ("mat the", "cap lai the", "lost card", "card replacement"),
        "steps": ("Mở biểu mẫu Báo mất / cấp lại thẻ, nêu thẻ bị mất và tình huống để BQL kiểm tra.",
                  "Xem lại rồi xác nhận gửi. Theo dõi ticket và phản hồi yêu cầu bổ sung của BQL.",
                  "Ứng dụng không tự khóa thẻ vật lý hoặc xác nhận đã phát hành thẻ mới."),
    },
    "visitor_registration": {
        "title": "Đăng ký khách đến chơi", "form": "visitor_registration",
        "aliases": ("khach", "nguoi nha den choi", "ban den choi", "ban ghe choi", "nguoi than den choi", "visitor", "visitors"),
        "steps": ("Mở Đăng ký khách, nhập tên khách và ngày đến hôm nay hoặc trong tương lai.",
                  "Xem lại và xác nhận gửi BQL, sau đó theo dõi Ticket đã gửi.",
                  "Nếu khách chuyển đến ở cùng hoặc ở qua đêm, chọn Thêm người cư trú thay vì khách thăm trong ngày."),
    },
    "move_in_schedule": {
        "title": "Đăng ký lịch chuyển đồ", "form": "move_in_schedule",
        "aliases": ("chuyen do", "chuyen nha", "chuyen hang", "mang do", "thang hang", "don vao can ho", "moving schedule", "move in schedule"),
        "steps": ("Mở Chọn lịch chuyển đồ, nhập ngày và giờ cụ thể dạng HH:MM, ghi chú đồ đạc nếu cần.",
                  "Có thể nhắn nhu cầu chuyển đồ tự nhiên trong chat, rồi bổ sung giờ như 14:00 ở tin tiếp theo. Ngày 20/10 được hiểu trong năm hiện tại; ngày đã qua hoặc nhiều ngày/giờ khác nhau cần làm rõ, không tự chọn.",
                  "Chọn giờ 00–23 và phút 00–59; form không dùng AM/PM và không có khối Điền nhanh từ giấy tờ.",
                  "Xem lại rồi xác nhận gửi BQL. Lịch đã gửi là đề nghị, cần BQL xem xét.",
                  "Nếu chưa chuyển đồ, chọn Chưa có nhu cầu, xem lại và xác nhận để hoàn thành checklist mà không tạo hay gửi ticket. Ticket đang xử lý cần theo dõi hoặc hủy đúng quy trình.",
                  "Theo dõi ticket để bổ sung hoặc điều chỉnh trên hồ sơ cũ khi còn mở."),
    },
    "internet_registration": {
        "title": "Đăng ký internet", "form": "internet_registration",
        "aliases": ("internet", "wifi", "wi fi", "lap mang", "nguoi len tu van", "nguoi tu van"),
        "steps": ("Mở Đăng ký internet, chọn nhu cầu và mô tả dịch vụ cần hỗ trợ.",
                  "Xem lại rồi xác nhận gửi BQL; theo dõi Ticket đã gửi.",
                  "Sẽ có người lên tư vấn sau khi BQL tiếp nhận và phối hợp sắp xếp. Không tự suy ra ngày giờ hoặc cam kết người tư vấn đến ngay.",
                  "Lưu ý đầy đủ chỉ nằm trong form đăng ký. Ticket internet đang xử lý hiển thị ngắn: cư dân thấy BQL sẽ cử người lên tư vấn; BQL thấy yêu cầu cử người phụ trách lên tư vấn. Thông báo này biến mất khi hoàn tất, từ chối hoặc hủy ticket.",
                  "BQL chỉ đánh dấu Hoàn tất khi vấn đề đã được giải quyết và được xác nhận giữa BQL với người tư vấn. Đã gửi hoặc đã duyệt chưa phải đã hoàn tất.",
                  "Ticket là yêu cầu hỗ trợ trong ứng dụng, chưa phải hợp đồng hay lịch lắp đặt được nhà mạng xác nhận."),
    },
    "confirm_regulations": {
        "title": "Đọc và xác nhận nội quy", "tab": "regulations",
        "aliases": ("noi quy", "doc noi quy", "xac nhan noi quy", "xac nhan da doc", "read regulations"),
        "steps": ("Mở Đọc tài liệu, đọc nội dung và chú ý nhãn nguồn: pháp luật tham khảo khác nội quy riêng của khu.",
                  "Bấm Xác nhận đã đọc ở cuối danh sách. Không cần xác nhận từng tài liệu.",
                  "Hệ thống lưu phiên bản và thời gian xác nhận; tài liệu cập nhật có thể cần đọc lại."),
    },
    "handover_inspection": {
        "title": "Hỗ trợ kiểm tra bàn giao", "form": "general_support",
        "aliases": ("ban giao", "chi so dien", "chi so nuoc", "handover"),
        "steps": ("Ghi lại vấn đề cần đối chiếu khi bàn giao và chỉ số điện/nước bạn quan sát được.",
                  "Mở Yêu cầu khác, mô tả nội dung và xác nhận gửi BQL để được hướng dẫn.",
                  "Phiên bản này chưa có biên bản bàn giao chuyên biệt; ticket hỗ trợ không tự hoàn tất mục kiểm tra bàn giao."),
    },
    "emergency_contact": {
        "title": "Cập nhật đầu mối liên hệ khẩn cấp", "form": "general_support",
        "aliases": ("nguoi lien he khan cap", "nguoi lien lac khan cap", "xac nhan lien he khan cap"),
        "steps": ("Chuẩn bị tên, mối quan hệ và số liên hệ của người bạn muốn BQL liên lạc khi cần.",
                  "Mở Yêu cầu khác để đề nghị BQL cập nhật; kiểm tra thông tin trước khi gửi.",
                  "Chưa có form chuyên biệt tự xác minh đầu mối; số liên hệ cá nhân không thay số cấp cứu."),
    },
    "safety_briefing": {
        "title": "Đọc hướng dẫn an toàn", "tab": "regulations",
        "aliases": ("huong dan an toan", "huong dan thoat hiem", "safety briefing"),
        "steps": ("Mở Đọc tài liệu, xem phần an toàn và nguồn dẫn.",
                  "Đối chiếu sơ đồ thoát hiểm, điểm tập kết và đầu mối tại tòa nhà; không suy ra vị trí từ văn bản pháp luật chung.",
                  "Khi có tình huống khẩn cấp tại Việt Nam: 115 cấp cứu y tế, 114 cháy/cứu nạn, 113 công an, 112 khẩn cấp quốc gia."),
    },
    "general_support": {
        "title": "Phản ánh / yêu cầu khác", "form": "general_support",
        "aliases": ("phan anh", "kien nghi", "bao hong", "bao su co", "khieu nai", "yeu cau khac", "tieng on", "on ao", "karaoke", "ro ri nuoc", "mat nuoc", "mat dien", "thang may hong", "ho boi", "tien ich", "general support", "complaint"),
        "steps": ("Mở Yêu cầu khác, chọn nhóm hỗ trợ và mô tả vấn đề, vị trí hoặc thời điểm liên quan.",
                  "Có thể kể sự cố hoặc phản ánh tiếng ồn trong chat để trợ lý chuẩn bị nội dung ticket. Kiểm tra bản nháp và bấm Xác nhận gửi; không phải chỉ sao chép lời nhắn. Trợ lý không gọi điện hoặc cam kết BQL đã xử lý.",
                  "Kiểm tra nội dung rồi xác nhận gửi BQL; theo dõi mã tại Ticket đã gửi.",
                  "Thông tin bổ sung gửi vào ticket cũ khi còn mở. BQL trả lời hoặc yêu cầu làm rõ trong luồng xử lý."),
    },
    "password": {
        "title": "Đổi mật khẩu",
        "aliases": ("doi mat khau", "change password"),
        "steps": ("Mở Thông tin cá nhân, bấm nút Đổi mật khẩu để mở form, nhập mật khẩu hiện tại, mật khẩu mới và xác nhận.",
                  "Mật khẩu mới từ 8 ký tự, khác mật khẩu hiện tại. Tài khoản cấu hình sẵn chỉ đổi trong cấu hình; form áp dụng cho tài khoản BQL cấp.",
                  "Đổi thành công, kiểm tra thông tin trong hộp kết quả rồi tự đóng; đăng nhập lại vì phiên cũ đã bị vô hiệu hóa."),
    },
    "notifications": {
        "title": "Theo dõi thông báo BQL", "tab": "notifications",
        "aliases": ("thong bao", "notification", "notifications"),
        "steps": ("Mở Thông báo BQL: mỗi ticket một thẻ, có cập nhật mới nhất và lịch sử theo ngày/giờ; tìm và lọc theo nhu cầu.",
                  "Bấm Đã đọc cho từng ticket hoặc Đã đọc tất cả. Thông báo đã đọc vẫn còn trong lịch sử.",
                  "Thông báo mới đến sau lần đánh dấu vẫn giữ trạng thái chưa đọc."),
    },
    "voice": {
        "title": "Giọng đọc trong chatbot",
        "aliases": ("giong doc", "giong noi", "doc tu dong", "nghe tu dong", "text to speech", "auto voice"),
        "steps": ("Bật Giọng đọc tự động trong chat trước khi gửi câu hỏi; trả lời mới sẽ được đọc khi âm thanh tải xong.",
                  "Tắt chế độ tự động để chỉ nghe khi bấm Nghe. Trình duyệt cần một thao tác chạm/bấm để cho phép phát âm thanh.",
                  "Nếu không nghe được, kiểm tra âm lượng, thiết bị phát và kết nối rồi thử nút Nghe."),
    },
    "requests": {
        "title": "Theo dõi / bổ sung ticket", "tab": "tickets",
        "aliases": ("theo doi ticket", "xem ticket", "xem yeu cau", "bo sung ho so", "huy ticket", "request status"),
        "steps": ("Mở Ticket đã gửi, tìm bằng mã, biển số hoặc nội dung; lọc loại, trạng thái và ngày gửi.",
                  "Mở ticket để xem lịch sử; bổ sung/gửi lại trên ticket cũ khi được phép hoặc hủy ticket còn mở.",
                  "Đã gửi, đang xem xét, cần bổ sung, đã duyệt và hoàn tất là các trạng thái khác nhau; không tự coi đã duyệt là xong."),
    },
}

RENTAL_CODES = ("verify_residency", "temporary_residency", "move_in_schedule", "resident_card", "confirm_regulations", "vehicle_registration", "internet_registration")

EN_GUIDES = {
    "account_registration": ("Register a dependent resident account", "Only the apartment owner can select an approved tenant or family member who does not already have an account. Review and confirm the request. Management reviews and issues credentials through the dedicated account flow; view the handover on the ticket and change the temporary password at first login."),
    "profile": ("Update resident profile", "Open Resident information and check your name, residency type, initial household size, vehicle and internet needs. Review and confirm saving. Self-reported information is not management approval; initial household size excludes approved additional temporary residents."),
    "verify_residency": ("Verify residence", "Open Residence profile, check your building/apartment and residency details, review and confirm sending to management. Confirming your information alone does not complete the procedure."),
    "temporary_residency": ("Add a resident", "Open Add resident, enter the person's name and 12-digit citizen ID, review and confirm sending. Follow Submitted tickets and Household. This application request does not replace official government residence registration."),
    "vehicle_registration": ("Register a vehicle", "Open Vehicle registration, select motorbike/car and enter the plate. Attach a JPG/PNG/WebP registration image up to 5 MB, including when entering details manually. Use one request and image per vehicle. Optional automatic image reading requires consent; check the extracted details before confirming. Follow Submitted tickets for management review."),
    "resident_card": ("Resident card", "Open Resident card and optionally select a preferred collection date and time using hour 00–23 and minute 00–59. Supply both or leave both empty if undecided. Review and confirm sending. The date and time are saved on the ticket for management review, not a confirmed appointment. This form has no document quick-fill section. Follow Submitted tickets; approved and completed are different statuses."),
    "card_replacement": ("Lost or replacement card", "Open Lost/replacement card, describe the card and situation, review and confirm sending. Follow the ticket; the application does not automatically block a physical card or issue a new one."),
    "visitor_registration": ("Register a visitor", "Open Visitor registration, enter the visitor's name and today's or a future visit date, review and confirm sending. For someone moving in or staying overnight, use Add resident instead."),
    "move_in_schedule": ("Schedule moving", "Open Moving schedule, choose a date, hour 00–23 and minute 00–59, review and confirm sending. The form has no document quick-fill section. The proposed slot needs management review; follow the existing ticket for changes."),
    "internet_registration": ("Internet request", "Open Internet registration, choose your service need, review and confirm sending. Full guidance appears only in the registration form. An active internet ticket shows a short resident notice that management will send an advisor, or a management reminder to assign one. The notice disappears when completed, rejected or cancelled. Do not invent an arrival time. Management marks the ticket completed only after the issue is resolved and confirmed between management and the advisor. A support request is not a confirmed installation appointment or provider contract."),
    "confirm_regulations": ("Read and acknowledge documents", "Open Documents, read the source labels and contents, then use the single acknowledgement button at the end. The application saves document revisions and acknowledgement time; updated documents may need another acknowledgement."),
    "handover_inspection": ("Handover support", "Record issues and observed electricity/water readings, then submit Other requests for management to check. There is no dedicated handover report in this version; a support ticket does not complete the handover checklist item."),
    "emergency_contact": ("Emergency contact details", "Prepare the contact's name, relationship and phone number, then request an update through Other requests. Management must check the details; this contact does not replace emergency services."),
    "safety_briefing": ("Safety guidance", "Read the safety references in Documents and check the actual building's escape map and assembly point. In Vietnam call 115 for medical emergencies, 114 for fire/rescue, 113 for police or 112 for national emergency assistance."),
    "general_support": ("Other requests", "Open Other requests, choose a support category and describe the issue, location or time. Review and confirm sending; follow the request ID in Submitted tickets. Add information to the existing open ticket."),
    "password": ("Change password", "Press Change password, enter your current password and a different new password of at least 8 characters. This form is for management-issued accounts. Review the result, close it yourself and sign in again because old sessions are invalidated."),
    "notifications": ("Management notifications", "Open Management notifications, view updates grouped by ticket with dates and times, and mark a ticket or all observed updates as read. Read history remains available and later updates stay unread."),
    "voice": ("Chat voice", "Enable automatic voice before sending a question; new answers play when audio is ready. Turn it off for manual Listen playback. Your browser needs a click/tap to allow audio; check volume, output device and connectivity if needed."),
    "requests": ("Track and update requests", "Open Submitted tickets, search by ID, plate or text, and filter by type, status or submission date. View history, supplement/resubmit the same ticket or cancel an eligible open request. Approved does not mean completed."),
}


def is_legal_question(query: str) -> bool:
    text = fold_text(query)
    return bool(re.search(r"\b(luat|law|decree|circular|vbhn)\b", text)) or any(contains_phrase(text, phrase) for phrase in ("phap luat", "nghi dinh", "thong tu", "can cu phap ly", "legal basis"))


def procedure_topics(query: str) -> list[str]:
    if is_legal_question(query):
        return []
    text = fold_text(query)
    codes = [code for code, guide in GUIDES.items() if any(contains_phrase(text, phrase) for phrase in guide["aliases"])]
    belongings = r"(?:do(?:\s+dac)?|noi that|tu lanh|sofa|ban ghe|hang hoa)"
    movement = r"(?:chuyen|van chuyen|mang|dua|khuan)"
    if not re.search(r"\bchuyen (?:khoan|tien)\b", text) and re.search(r"\b" + movement + r"\b(?:\s+\w+){0,5}\s+" + belongings + r"\b|\b" + belongings + r"\b(?:\s+\w+){0,5}\s+" + movement + r"\b", text):
        codes.append("move_in_schedule")
    if re.search(r"\bhang xom\b.{0,70}\b(?:mo nhac|hat|on|tiec)\b", text):
        codes.append("general_support")
    if not codes and any(contains_phrase(text, phrase) for phrase in ("goi bql", "goi ban quan ly", "bao bql", "bao ban quan ly", "lien he bql", "lien he ban quan ly")):
        codes.append("general_support")
    if re.search(r"\b(?:dang ky|dang ki|dky|dki|dk)\b(?:\s+\w+){0,5}\s+xe\b", text):
        codes.append("vehicle_registration")
    if "card_replacement" in codes:
        codes = [code for code in codes if code != "resident_card"]
    if "vehicle_registration" in codes and not any(contains_phrase(text, phrase) for phrase in ("the cu dan", "the dan cu", "the ra vao", "resident card")):
        codes = [code for code in codes if code != "resident_card"]
    if "temporary_residency" in codes:
        codes = [code for code in codes if code != "visitor_registration"]
    if "account_registration" in codes:
        codes = [code for code in codes if code != "temporary_residency"]
    return list(dict.fromkeys(codes))


def guide_codes(query: str, category: str | None = None) -> list[str]:
    if is_legal_question(query):
        return []
    text = fold_text(query)
    if any(contains_phrase(text, phrase) for phrase in ("cho thue", "thue nha", "thue can ho", "nguoi thue", "rental", "rentals", "tenant", "tenants")):
        return list(RENTAL_CODES)
    if category in GUIDES:
        return [category]
    # Specific intents precede overlapping aliases (lost card, overnight guests).
    topics = procedure_topics(query)
    for code in ("card_replacement", "temporary_residency", "emergency_contact", "vehicle_registration"):
        if code in topics:
            return [code]
    return topics


def wants_guidance(query: str) -> bool:
    text = fold_text(query)
    return any(contains_phrase(text, phrase) for phrase in ("thu tuc", "quy trinh", "huong dan", "giay to", "can gi", "can nhung", "lam sao", "nhu the nao", "cach", "bao nhieu", "bao lau", "may ngay", "muc phi", "khi nao", "bao gio", "o dau", "cho nao", "ai hoan tat", "ai danh dau", "lich nhan the", "ngay gio nhan the", "how", "where", "guide", "what documents", "fee", "deadline", "who completes", "when can"))


def application_guidance(query: str, category: str | None = None) -> dict | None:
    codes = guide_codes(query, category)
    if not codes or not (wants_guidance(query) or category in GUIDES):
        return None
    confirm_details = bool(re.search(r"\b(phi|fee|cost|sla)\b|bao nhieu|bao lau|may ngay|thoi han", fold_text(query)))
    needs_management = any(GUIDES[code].get("form") and code != "profile" for code in codes)
    return {
        "answer_status": "application_guidance", "data_partition": "application",
        "guides": [{"code": code, **GUIDES[code], "title_en": EN_GUIDES[code][0], "steps_en": [EN_GUIDES[code][1]], "source_ref": "web/app.js; web/resident-workflows.js; src/api/routes.py"} for code in codes],
        "requires_human": confirm_details, "sources": [],
        "source_notice": "Hướng dẫn dựa trên chức năng MoveInMate đang triển khai. Quy định riêng của tòa nhà cần tài liệu BQL đúng khu đã xác minh.",
        "unverified_details": ["Phí, thời hạn xử lý, giờ tiếp nhận và giấy tờ bổ sung riêng của tòa nhà cần BQL xác nhận; ứng dụng không tự đặt các thông tin này."] if needs_management else [],
    }


def knowledge_partitions() -> dict:
    """Static reference inventory; contains no resident information or receipts."""
    return {
        "guides": [{"code": code, **guide, "data_partition": "application"} for code, guide in GUIDES.items()],
    }


def legal_category(query: str) -> str | None:
    text = fold_text(query)
    for category, phrases in (
        ("fire_safety", ("phong chay", "chua chay", "pccc", "thoat hiem", "fire")),
        ("residency", ("tam tru", "cu tru", "can cuoc", "cccd", "residence", "residency", "resident")),
        ("waste_disposal", ("rac thai", "moi truong", "waste")),
        ("housing", ("nha o", "cho thue", "thue nha", "can ho", "chung cu", "housing")),
    ):
        if any(contains_phrase(text, phrase) for phrase in phrases):
            return category
    return None
