# CP-Kelompok-C---PRIMASIS
Capstone Project - PRIMASIS

## Kedai Primadona Sistem Informasi Konsinyasi

PRIMASIS membantu mencatat stok titipan, penjualan, retur, dan laporan margin untuk SMK Muhammadiyah 4 serta SMAN Mojo.

### Menjalankan

Gunakan Python 3.10 atau lebih baru:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py setup-users
python app.py
```

Perintah setup akan meminta kata sandi untuk akun `chusnul`, `smkmuh4`, dan `smanmojo`. Kata sandi minimal 10 karakter; gunakan kata sandi unik dan lebih panjang untuk penggunaan bersama. Aplikasi tersedia di `http://localhost:5000`. Database SQLite dibuat sebagai `primasis.db`; kunci sesi lokal berada di `instance/.secret_key`.

Data transaksi dan akun disimpan di server, bukan di browser. Untuk penggunaan jaringan/produksi, jalankan di balik HTTPS, set `PRIMASIS_COOKIE_SECURE=1`, gunakan server WSGI produksi, dan batasi akses jaringan. Jangan hapus database untuk mempertahankan data.

### Hosting gratis di Render

Repository ini menyertakan `render.yaml` untuk membuat web service publik Render dengan Gunicorn dan HTTPS menggunakan paket **Free**. Layanan gratis dapat tidur saat tidak digunakan dan memerlukan waktu untuk aktif kembali saat dibuka. Penyimpanan paket gratis bersifat sementara: database SQLite dapat hilang saat service restart atau deploy. Gunakan hanya untuk demo/uji coba, bukan untuk menyimpan transaksi penting.

1. Push repository ke GitHub.
2. Di Render, pilih **New > Blueprint**, hubungkan repository ini, lalu deploy blueprint.
3. Tunggu hingga deploy selesai dan buka URL `*.onrender.com` yang disediakan Render.
4. Pada pembukaan pertama, siapkan tiga kata sandi akun melalui halaman setup PRIMASIS. Gunakan kata sandi unik dan jangan membagikannya.

Alamat Render dapat langsung dibuka sebagai situs web. Domain khusus dapat ditambahkan melalui pengaturan service Render. SQLite pada konfigurasi satu worker ini cocok untuk demo ringan; jangan menambah instance atau worker tanpa migrasi ke database server seperti PostgreSQL. Untuk data yang perlu dipertahankan, gunakan paket/hosting dengan penyimpanan persisten dan siapkan backup.

Laporan menghitung laba dari produk yang benar-benar terjual. Stok yang masih berada di koperasi tercatat sebagai sisa dan belum dihitung sebagai laba terealisasi.

Mitra dapat ditambahkan oleh admin melalui menu **Mitra** dengan membuat username dan kata sandi. Akun mitra merekap jumlah retur/sisa pada sore hari; jumlah terjual dan total setoran ke Kedai Primadona dihitung otomatis berdasarkan harga setor produk.
