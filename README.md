# MoveInMate

Ứng dụng hỗ trợ cư dân làm thủ tục căn hộ và Ban quản lý tiếp nhận, yêu cầu bổ sung, duyệt và hoàn thành ticket. Trợ lý hỗ trợ hỏi đáp có nguồn, đọc tệp/ảnh để chuẩn bị biểu mẫu và nhắc thông tin còn thiếu; cư dân luôn kiểm tra, xác nhận trước khi gửi.

Backend dùng FastAPI, LangGraph và PostgreSQL. Giao diện dùng HTML/CSS/JavaScript, đăng nhập nội bộ với JWT.

Đây là snapshot mã ứng dụng, không kèm database đang sử dụng, tệp cư dân, API key, mật khẩu vận hành hoặc cấu hình AWS riêng. Push vào repository này không tự triển khai web AWS.

## Agent và RAG

LangGraph gọi công cụ theo quyền của người đăng nhập, xếp hàng các lượt trong cùng hội thoại, giới hạn số bước và ngăn thực thi lặp công cụ trong một lượt. Lịch sử được nén theo nhóm gọi công cụ đầy đủ để không làm hỏng dữ liệu đầu vào hoặc bỏ mất kết quả.

RAG tìm trên tài liệu PostgreSQL bằng BM25 kết hợp cụm từ và từ khóa tiếng Việt. Bộ lọc khu, loại nguồn và trạng thái xác minh được áp dụng trước khi chọn kết quả; trích dẫn giữ thông tin đoạn và trang. Bản này không dùng Chroma hoặc thêm API embedding trả phí; biến `CHROMA_PERSIST_DIR` được giữ để tương thích cấu hình cũ, không cần xóa thư mục chỉ mục cũ.

Các giới hạn hội thoại hiện áp dụng trong một worker, chưa phải khóa phân tán hay checkpoint bền vững. Kiểm thử tự động không thay thế bộ câu hỏi nghiệp vụ được BQL gán nhãn; chưa có tỷ lệ độ chính xác RAG được xác nhận.

## Cấu trúc

| Thư mục / tệp | Nội dung |
|---|---|
| `src/` | API, phân quyền, nghiệp vụ, agent, RAG và truy cập PostgreSQL |
| `web/` | Giao diện cư dân, Ban quản lý và tài nguyên mascot |
| `alembic/` | Toàn bộ migration database |
| `scripts/` | Migration, seed demo, nguồn công khai, danh mục khu và backup |
| `tests/`, `eval/fixtures/` | Kiểm thử và dữ liệu tổng hợp phục vụ kiểm thử |
| `Dockerfile`, `docker-compose.yml` | Backend và PostgreSQL chạy bằng Docker |
| `requirements-runtime.txt`, `requirements.txt` | Dependency chạy ứng dụng và bộ công cụ phát triển/kiểm thử |
| `.env.example` | Mẫu cấu hình; các khóa và mật khẩu riêng để trống |

## Chạy bằng Docker Desktop

Yêu cầu Docker Desktop chạy Linux containers và Docker Compose v2. Các lệnh dưới đây dùng PowerShell.

### 1. Lấy mã và tạo cấu hình

```powershell
git clone https://github.com/Neon310304/Neon.git
Set-Location Neon
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Điền `.env` trước khi khởi động:

| Biến | Cấu hình |
|---|---|
| `POSTGRES_PASSWORD` | Mật khẩu DB riêng; dùng chữ, số, `_`, `-` để phù hợp URL Compose |
| `POSTGRES_DB`, `POSTGRES_TEST_DB` | Giữ `moveinmate_dev`, `moveinmate_test`; DB test phải khác DB ứng dụng |
| `LOCAL_AUTH_ADMIN_PASSWORD`, `LOCAL_AUTH_USER_PASSWORD` | Mật khẩu BQL và cư dân bootstrap, tối thiểu 8 ký tự |
| `AUTH_JWT_SECRET` | Chuỗi ngẫu nhiên riêng, ít nhất 32 ký tự |
| `SETTINGS_ENCRYPTION_KEY` | Khóa Fernet riêng để mã hóa cấu hình/bàn giao tài khoản; không thay tùy tiện khi DB đã có dữ liệu |
| `LLM_PROVIDER` và khóa/model tương ứng | Cần cho hội thoại và đọc ảnh bằng model; để trống khóa không tạo kết nối AI thật |
| `DATABASE_URL`, `TEST_DATABASE_URL` | Khi chạy Python trên máy: thay `your-password` bằng mật khẩu DB, chỉnh cổng nếu cần; Docker Compose tự đặt URL nội bộ |
| `TTS_PROVIDER`, `STT_PROVIDER` | Chọn nhà cung cấp được phép sử dụng, điền khóa hoặc URL riêng trong `.env`; không đặt trong frontend |

Giữ `AUTH_MODE=required`, `ALLOW_DEMO_AUTH=false`, `SHOW_LOCAL_LOGIN_CREDENTIALS=false`. Reset mặc định tắt; chỉ bật `LOGIN_RESET_ENABLED` và đặt `LOGIN_RESET_CODE` trên DB demo có chủ đích. Chức năng Reset chỉ dành cho BQL đã đăng nhập và xóa dữ liệu nghiệp vụ toàn ứng dụng sau xác nhận.

Compose dùng project `moveinmate-neon`, tạo volume riêng thay vì dùng lại project MoveInMate khác. Nếu máy đang dùng cổng 8000/5432, đổi `BACKEND_PORT`/`POSTGRES_PORT` và URL trên host trong `.env` trước khi chạy.

### 2. Build và tạo khóa riêng

```powershell
docker compose config --quiet
docker compose build backend
docker compose run --rm --no-deps backend python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose run --rm --no-deps backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Chép hai kết quả vào `AUTH_JWT_SECRET` và `SETTINGS_ENCRYPTION_KEY` trong `.env`. Không gửi hoặc commit các giá trị đó.

### 3. Tạo DB và dữ liệu khởi đầu

```powershell
docker compose up -d --wait db
docker compose run --rm --no-deps backend python -m scripts.migrate_database
docker compose run --rm --no-deps backend python -m scripts.seed_demo
docker compose run --rm --no-deps backend python -m scripts.import_residential_sites
docker compose up -d --wait --wait-timeout 180 backend
docker compose ps
Invoke-RestMethod http://localhost:8000/api/v1/health
```

`seed_demo` chỉ dùng trên DB demo mới: tạo cư dân tổng hợp R001, checklist và tình huống ticket mẫu. Không chạy seed/reset trên DB vận hành. Có thể tạo thêm các tài khoản demo bằng `docker compose run --rm --no-deps backend python -m scripts.seed_sample_accounts`; đây là dữ liệu thử, không phải tài khoản người thật.

Mở <http://localhost:8000>, dùng tên `admin`/`user` và mật khẩu đã đặt trong `.env`. Nếu đổi `BACKEND_PORT`, đổi cổng trong URL. Tài liệu API: <http://localhost:8000/docs>.

### 4. Nạp nguồn tri thức công khai

```powershell
docker compose exec -T backend python scripts/crawl_data.py
docker compose restart backend
```

Danh sách URL gốc ở `scripts/public_sources.json`; danh mục tên khu và nguồn ở `scripts/residential_sites.json`. Crawler chỉ tải từ danh sách host công khai cho phép. Tài liệu pháp luật công khai không đồng nghĩa nội quy riêng của từng khu đã được BQL xác minh. Thư mục `src/project_packs/knowledge/` chứa tài liệu mẫu và mặc định không được dùng như nguồn thật (`INCLUDE_DEMO_KNOWLEDGE=false`).

## Kiểm tra

Backend có kiểm thử PostgreSQL trên `moveinmate_test`, tách khỏi DB ứng dụng. Script khởi tạo DB test chạy khi volume PostgreSQL được tạo lần đầu. Test API dùng dữ liệu tổng hợp và thay thế kết nối nhà cung cấp khi cần, không chứng minh độ chính xác tuyệt đối của OCR/model thật.

Image Docker chỉ cài dependency runtime, không chứa pytest, Ruff hoặc trình cài gói. Chạy kiểm thử bằng Python 3.11 trở lên trên máy, sau khi cấu hình `DATABASE_URL` và `TEST_DATABASE_URL` trong `.env` theo cổng PostgreSQL trên host. DB test phải khác DB ứng dụng; fixture có thao tác xóa dữ liệu DB test.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:APP_ENV = 'test'
.\.venv\Scripts\python.exe -m pytest tests/test_agent_orchestration.py tests/test_context_tool_history.py tests/test_rag_retrieval.py tests/test_attachments.py tests/test_form_extraction.py tests/test_request_drafts.py tests/test_public_speech.py tests/test_private_speech_config.py -q
Remove-Item Env:APP_ENV
```

Kiểm thử JavaScript cần Node.js 22 trở lên:

```powershell
node --test tests/web/*.test.mjs
```

## Truy vấn PostgreSQL chỉ đọc

```powershell
docker compose exec db psql -U moveinmate -d moveinmate_dev
```

```sql
SELECT version_num FROM alembic_version;
SELECT status, COUNT(*) FROM management_requests GROUP BY status ORDER BY status;
SELECT code, required, status FROM checklist_tasks WHERE resident_id = 'R001' ORDER BY id;
SELECT COUNT(*) AS documents FROM knowledge_documents;
SELECT COUNT(*) AS chunks FROM knowledge_chunks;
```

## Dữ liệu và bảo mật

- PostgreSQL lưu trong volume `moveinmate-neon_postgres_data`; volume `moveinmate-neon_backend_runtime` giữ thư mục runtime tương thích cấu hình cũ. Tệp tải về nằm trong thư mục `data/` trên máy chạy.
- `docker compose down` giữ volume; `docker compose down -v` xóa dữ liệu. Không xóa volume để vượt lỗi migration; cần backup trước khi nâng cấp DB đã có dữ liệu.
- Thay `.env` rồi dùng `docker compose up -d --no-deps backend` để nạp lại; `restart` không nạp biến môi trường mới.
- Không commit `.env`, khóa riêng, bản backup DB, giấy tờ cư dân hoặc tệp upload. Chỉ `.env.example` được chia sẻ.
- Docker mặc định chỉ mở cổng trên `127.0.0.1`. Triển khai công khai cần HTTPS, cấu hình proxy tin cậy, mật khẩu riêng và kiểm soát hạn mức/chi phí nhà cung cấp.

## Giấy phép

Giữ giấy phép MIT của dự án trong `LICENSE` và giấy phép thư viện mascot trong `web/vendor/open-mascot/LICENSE`. Nguồn thư viện: <https://github.com/0oooh/open-mascot-studio>.
