# Open Mascot — nguồn thư viện

- Studio: https://open-mascot-studio.web.app/
- Repository: https://github.com/0oooh/open-mascot-studio
- Phiên bản nguồn: commit `b83ca3863f4e35424342d42538873af1bacddf9f`.
- Giấy phép MIT được giữ nguyên trong `LICENSE`.
- Chỉ lấy 5 module SVG cần cho trình duyệt, giữ nguyên nội dung; không lấy studio,
  analytics, dịch vụ Firebase hoặc tài nguyên từ CDN khi người dùng mở MoveInMate.
- `package.json` local chỉ đánh dấu ESM để kiểm thử Node, không thêm phụ thuộc npm.
- Mascot Mallow giữ hình dạng soft và màu cam đào của studio; nền SVG được ẩn
  qua CSS để đặt trên kính. Wrapper và tùy chọn giảm chuyển động do MoveInMate quản lý.
