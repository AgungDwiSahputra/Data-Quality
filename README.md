# Validasi Kualitas Data — 9 Dimensi

Validasi otomatis dataset parquet KPN Plantation Group memakai Great Expectations.
Berlaku untuk berkas lokal maupun objek di S3.

Setiap skrip inti (`scaffold.py`, `build_reference.py`, `validate.py`) menerima
`--config <berkas>.yml`, jadi layer/lapisan data yang berbeda bisa punya daftar
dataset sendiri tanpa bercampur — mis. `datasets.yml` untuk layer gold,
`bronze_datasets.yml` untuk layer bronze. Tanpa `--config`, ketiganya memakai
`datasets.yml`.

---

## Ringkas: file mana yang saya ubah?

| Kebutuhan | File yang diubah |
|---|---|
| **Menambah dataset baru** | `datasets.yml` — **hanya ini** |
| Mengubah ambang batas satu dataset | `datasets.yml`, blok `ambang:` |
| Mengubah ambang batas semua dataset | `datasets.yml`, blok `default:` |
| Menambah kredensial AWS / nama bucket | `.env` (buat sendiri, tidak masuk Git) |
| Menambah kredensial SQL Server (dimensi 9) | `.env`, variabel `SQLSERVER_*` |
| Mengaktifkan rekonsiliasi ke SQL Server | `datasets.yml`, blok `sumber.sql_server:` |
| Menambah dataset di layer lain (bronze, dst) | file config baru, mis. `bronze_datasets.yml` |
| Menambah **jenis** pemeriksaan baru | `dqcore/checks.py` — jarang perlu |

**Folder `dqcore/` tidak perlu disentuh untuk menambah dataset.** Itu mesinnya.
Polanya sama dengan Fase 1: `jobs.py` yang diedit, `exporter.py` tidak.

---

## Menambah dataset baru — 3 langkah

### 1. Hasilkan daftar kolomnya otomatis

Jangan mengetik daftar kolom manual. Biarkan `scaffold.py` membacanya dari file
parquet yang sudah ada:

```bash
python scaffold.py --nama awl --tempel \
    --glob "D:/Workspace Agung/LEARNING/Parquet/Phase 1/LoadParquetProcess/output/gold_awl_readings_*.parquet"
```

`--tempel` menyisipkan hasilnya langsung ke akhir `datasets.yml`. Tanpa `--tempel`,
blok dicetak ke layar untuk disalin sendiri.

Bisa juga langsung dari S3:

```bash
python scaffold.py --nama awl --s3 "s3://bucket/datalake/gold/awl"
```

Selain nama & tipe kolom (dibaca langsung dari berkas, jadi pasti benar),
scaffold **menebak**: surrogate key, kunci bisnis, kolom waktu, granularitas,
kolom kode yang layak jadi referensi, relasi 1:1 antar kolom, dan flag boolean.
Semua tebakan ditandai `TEBAKAN` di komentarnya.

### 2. Rapikan hasilnya di `datasets.yml`

Yang perlu Anda periksa:

- `deskripsi:` — masih berisi `TODO`
- baris `s3:` — salin dari katalog di bagian bawah `datasets.yml`
- baris bertanda `TEBAKAN` — hapus yang tidak benar
- **kolom ukuran** (curah hujan, tinggi air, dsb): ubah ke bentuk panjang dan
  isi batas fisiknya. Inilah satu-satunya bagian yang tidak bisa ditebak mesin,
  karena butuh pengetahuan domain:

```yaml
      - nama: water_level_cm
        tipe: decimal
        wajib: false
        rentang: [0, 1000]        # batas yang MUNGKIN secara fisik,
        satuan: "cm"              # bukan yang kebetulan terlihat di data
        profil_distribusi: true   # <- mengaktifkan dimensi DISTRIBUTION
```

Tanpa `rentang` + `profil_distribusi`, dimensi DISTRIBUTION dilewati dan
alasannya dicatat di laporan.

### 3. Bangun baseline, lalu validasi

```bash
python build_reference.py --dataset awl
python validate.py --dataset awl
```

---

## Perintah sehari-hari

```bash
python validate.py --list                    # dataset terdaftar + status profilnya
python validate.py --dataset ars             # partisi terbaru, dari berkas lokal
python validate.py --dataset ars --date 2026-01-14
python validate.py --dataset ars --source s3 # baca langsung dari S3
python validate.py --dataset ars --mode harian   # SLA pipeline harian (bukan backfill)
python validate.py --dataset ars --sweep     # semua partisi, ringkasan per partisi
python validate.py --all                     # semua dataset sekaligus
python validate.py --dataset ars --open-docs # buka laporan HTML di browser
python validate.py --dataset ars --no-reconcile   # lewati dimensi 9 (tanpa VPN/akses SQL Server)

# Layer lain (config terpisah, mis. bronze):
python validate.py --config bronze_datasets.yml --dataset iot_wm_transaction --source s3
```

Kode keluar `1` bila ada kegagalan **blocking** — cocok dipakai sebagai gerbang CI.

---

## Struktur folder

```
datasets.yml          <- file yang Anda edit untuk layer gold
bronze_datasets.yml   <- file terpisah untuk layer bronze (sama-sama dibaca lewat --config)
scaffold.py              hasilkan blok YAML dari parquet
validate.py              jalankan validasi
build_reference.py       bangun baseline
dqcore/                  mesin — tidak perlu disentuh
  spec.py                  membaca datasets.yml
  sources.py               resolusi partisi lokal & S3
  profile.py               baseline statistik + penurunan pola string
  checks.py                menurunkan 9 dimensi dari spec
  sqlserver.py             koneksi SQL Server, khusus dimensi 9 (opsional)
  report.py                laporan terminal / Markdown / JSON
  docs.py                  situs HTML Data Docs
profiles/<ds>.json       baseline per dataset (ikut Git — agar reproducible)
reports/                 hasil validasi (diabaikan Git)
gx/                      situs Data Docs + riwayat antar-run
```

---

## Apa yang masuk Git, apa yang tidak

| Masuk Git | Alasan |
|---|---|
| `*.py`, `dqcore/` | kode — mesinnya |
| `datasets.yml.example`, `bronze_datasets.yml.example` | contoh generik/placeholder, aman diversikan |
| `.env.example` | contoh kredensial berisi placeholder, bukan nilai asli |
| `profiles/*.json` | baseline — harus ikut terversi agar validasi reproducible di mesin lain |
| `gx/expectations/`, `gx/checkpoints/`, `gx/validation_definitions/`, `gx/great_expectations.yml` | definisi aturan validasi (bukan hasilnya) |
| `requirements.txt`, `README.md`, `CLAUDE.md`, `.gitignore` | dokumentasi & dependensi |

| TIDAK masuk Git | Kenapa | Cara dapatkan saat clone awal |
|---|---|---|
| `.env` | kredensial AWS + SQL Server | salin dari `.env.example`, isi nilai sebenarnya |
| `datasets.yml`, `bronze_datasets.yml` | struktur data internal organisasi: nama tabel SQL Server, nama bucket S3, nama kolom bisnis, kode wilayah/unit | salin dari `datasets.yml.example` / `bronze_datasets.yml.example`, isi dataset Anda sendiri |
| `reports/` | hasil, ditimpa tiap run | tidak perlu disiapkan — dibuat otomatis oleh `validate.py` |
| `gx/uncommitted/` | riwayat Data Docs + cache internal GX | tidak perlu disiapkan — dibuat otomatis oleh Great Expectations saat `validate.py` jalan pertama kali |
| `__pycache__/`, `*.pyc` | cache bytecode Python | tidak perlu — dibuat otomatis |
| `.venv/` / `venv/` / `env/` | virtual environment | `python -m venv .venv` lalu install `requirements.txt` |
| `.vscode/`, `.idea/`, `Thumbs.db`, `.DS_Store` | spesifik ke IDE/OS masing-masing | tidak perlu |

> **Catatan riwayat:** `datasets.yml` dan `bronze_datasets.yml` sudah pernah ter-commit
> (dan ter-push) sebelum aturan di atas ditambahkan. Menambahkannya ke `.gitignore`
> hanya mencegah commit *berikutnya* — riwayat commit lama masih menyimpan versi
> lengkapnya sampai riwayatnya dibersihkan secara terpisah (`git filter-repo`/BFG,
> atau buat ulang repo dari commit yang sudah bersih).

### Setup dari clone kosong

```bash
git clone <url-repo>
cd "Great Expectations"

python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env                          # Linux/Mac: cp .env.example .env
copy datasets.yml.example datasets.yml          # Linux/Mac: cp ...
copy bronze_datasets.yml.example bronze_datasets.yml
# lalu isi .env (AWS_* hanya untuk --source s3, SQLSERVER_* hanya untuk dimensi 9)
# dan isi datasets.yml / bronze_datasets.yml dengan dataset Anda sendiri

python validate.py --list       # jalan tanpa .env sama sekali (dataset lokal)
```

---

## Sembilan dimensi yang diperiksa

| Dimensi | Diaktifkan oleh | Contoh yang ditangkap |
|---|---|---|
| **1. Schema** | `kolom:` | kolom hilang/berganti nama, tipe data bergeser (schema evolution), kolom asing muncul |
| **2. Volume** | otomatis | data hilang sebagian, pemuatan ganda |
| **3. Freshness** | `kunci.waktu` | data basi, timestamp masa depan, jam bolong |
| **4. Missingness** | `wajib:` | null mendadak, string kosong tersembunyi |
| **5. Uniqueness** | `kunci.surrogate`, `kunci.bisnis` | duplikat kunci bisnis, duplikat per jam |
| **6. Integrity** | `referensi:`, `relasi:`, `sama_nilai:`, `urutan_waktu:`, `flag_dq:` | kode tak dikenal, mapping bergeser, tanggal kirim terjadi sebelum tanggal pesan |
| **7. Distribution** | `profil_distribusi:` | sensor macet, pergeseran level, bentuk sebaran berubah |
| **8. Data Cleanliness & Encoding** | `kolom:` (string) | format ID menyimpang, karakter kontrol, mojibake dari konversi kolasi SQL Server -> UTF-8 |
| **9. Rekonsiliasi Sumber** | `sumber.sql_server.tabel:` | baris tercecer/terduplikasi saat pindah ke bronze, kolom sumber yang tidak terbawa |

Dimensi yang tidak punya bahan **dilewati dan dicatat alasannya** — tidak pernah
terlihat seperti lulus.

Dimensi 1 **sengaja tidak memeriksa urutan kolom**. Parquet bersifat read-by-name
(nama kolom tersimpan di metadata file, dibaca berdasarkan nama oleh Spark/Athena/
pandas/dst) — berbeda dari CSV yang memang rawan salah baca kalau urutan bergeser.
Menegakkan urutan di sini hanya akan menghasilkan kegagalan blocking palsu (mis.
setelah scaffold.py dijalankan ulang) tanpa konsumen mana pun yang benar-benar
rusak. Fokusnya sebaliknya ke **schema evolution**: kolom yang hilang/berganti
nama, kolom asing yang muncul, dan — yang paling berbahaya karena diam-diam —
tipe data sebuah kolom yang bergeser walau namanya tetap sama (mis. kolom INT di
sumber tiba-tiba terbaca STRING di bronze).

Dimensi 6 murni bicara **hubungan antar data** — dua kategori: *Referential
Integrity* (`referensi:`, `relasi:` — nilai/pemetaan kode valid terhadap master)
dan *Business Logic Consistency* (`sama_nilai:`, `urutan_waktu:`, dan `flag_dq:`
varian `kolom:`/`aturan:` — kolom-kolom yang secara bisnis wajib selaras, mis.
tanggal kirim tidak boleh sebelum tanggal pesan). Entri `flag_dq:` varian
`kolom:`/`aturan:` di sini bukan "audit kejujuran flag DQ" — itu cuma satu bentuk
Business Logic Consistency: flag boolean yang wajib sinkron dengan kondisi pada
kolom lain. Varian `flag_dq:` yang lain (`harus:` — nilai konstan sepanjang
partisi) tetap di dimensi **5. Uniqueness**, karena itu memang mengaudit
keseragaman sinyal QA dari pipeline hulu, bukan relasi antar kolom.

Dimensi 8 **berganti nama** dari "Unstructured (Data)" menjadi **Data Cleanliness &
Encoding** — nama lama menyesatkan, karena data tujuan berformat Parquet yang
tabular/terstruktur, bukan data tak terstruktur (free text, log, gambar). Pemicu
checknya tidak berubah: mendeteksi pola string yang menyimpang, panjang di luar
kebiasaan, karakter kotor (`POLA_KOTOR` di `dqcore/checks.py`), dan yang paling
sering terjadi — mojibake akibat konversi kolasi SQL Server 2012 (biasanya
`SQL_Latin1_General`) ke UTF-8 saat data dipindahkan ke S3.

Dimensi 9 berbeda dari delapan lainnya: nilai pembandingnya (row count, daftar
kolom) diambil **langsung dari SQL Server** saat validasi berjalan, bukan dari
baseline `profiles/*.json`. Karena itu ia butuh koneksi jaringan/VPN + kredensial
SQL Server (lihat "Akses SQL Server" di bawah) — kalau belum tersedia, dimensi
ini dilewati dengan alasan yang mengutip error koneksinya, bukan menghentikan
validasi dataset lain. Lewati secara sengaja dengan `--no-reconcile`.

---

## Dua jenis laporan

| | Data Docs (HTML) | `reports/*.md` |
|---|---|---|
| Asal | bawaan Great Expectations | disusun skrip ini |
| **Riwayat antar-run** | **ya** | tidak, ditimpa |
| Severity blocking/warning | tidak dikenal GX | ya |
| Catatan penjelas + batasan | tidak | ya |

Keduanya saling melengkapi. Mode `--sweep` sengaja tidak membangun Data Docs
supaya cepat.

---

## Akses S3

Buat berkas `.env` di folder ini (tidak masuk Git):

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-southeast-1
S3_BUCKET_NAME=nama-bucket
```

`${S3_BUCKET_NAME}` di `datasets.yml` diganti otomatis. Tanpa berkas ini,
`--list` dan validasi lokal tetap jalan; hanya `--source s3` yang gagal, dengan
pesan yang menyebut variabel mana yang kurang.

---

## Akses SQL Server

Hanya diperlukan oleh dimensi **9. Rekonsiliasi Sumber** — dilewati otomatis
kalau dataset tidak mendeklarasikan `sumber.sql_server.tabel`, atau kalau
dijalankan dengan `--no-reconcile`. Tambahkan ke `.env` di folder ini:

```
SQLSERVER_HOST=...
SQLSERVER_DB=...
SQLSERVER_UID=...
SQLSERVER_PWD=...
```

Pola koneksinya SAMA dengan Fase 1 (`pyodbc` + ODBC Driver 17 for SQL Server,
SQL Authentication) — tapi `.env` ini terpisah, tidak dibaca otomatis dari
folder Fase 1. ODBC Driver 17 harus terinstal di OS, bukan hanya paket
Python-nya. Aktifkan per dataset lewat blok berikut di `datasets.yml`:

```yaml
sumber:
  sql_server:
    tabel: "dbo.T_IOT_WM_Transaction"
    kolom_waktu: recordTime   # kolom WHERE filter; kosongkan untuk full-table count
    offset_jam: 7             # samakan dengan window job ingest yang menulis ke bronze
```

`offset_jam` WAJIB disamakan dengan window WHERE clause job ingest yang
sebenarnya (lihat `jobs.py` Fase 1) — kalau beda, row count SQL Server vs
bronze tidak akan pernah cocok meski datanya benar.

---

## Yang membatasi kesimpulan validasi

1. **Daftar kode master bukan sumber resmi.** Blok `master` di `profiles/*.json`
   adalah union kode dari data historis, bukan hasil query ke
   `T_COR_EstateMapping_New` / master block / master device. Artinya dimensi
   INTEGRITY mendeteksi *kode yang belum pernah muncul*, bukan *kode yang tidak
   sah menurut master*. Untuk gerbang produksi, validasi dulu dataset master-nya
   (lihat katalog di `datasets.yml`), lalu pakai itu sebagai sumber kebenaran.

2. **Ambang batas distribusi diturunkan dari data, bukan dari standar.** Rentang
   fisik yang Anda tulis di `datasets.yml` bersifat pasti; koridor mean, stdev,
   dan kuantil berasal dari partisi historis dan perlu dikonfirmasi ke pemilik data.

3. **Baseline hanya sepanjang data yang tersedia.** Pola musiman tahunan belum
   terwakili, jadi koridor distribusi bisa terlalu sempit saat musim berganti.
   Bangun ulang baseline secara berkala.

4. **Volume (dimensi 2) masih koridor dari baseline, bukan rekonsiliasi.** Untuk
   dataset yang belum mendeklarasikan `sumber.sql_server.tabel`, koridornya
   hanya dari baseline berkas parquet historis. Dataset yang SUDAH mendeklarasikan
   `sumber.sql_server` mendapat pembanding row count sungguhan lewat dimensi
   **9. Rekonsiliasi Sumber** — tapi itu row count `COUNT(*)` biasa terhadap
   satu tabel, belum tentu mencakup semua transformasi/join yang terjadi di
   layer di atas bronze.

---

## Pemasangan

```bash
pip install -r requirements.txt
```

`boto3` hanya diperlukan bila memakai `--source s3`.
`pyodbc` hanya diperlukan oleh dimensi 9 (rekonsiliasi ke SQL Server) — butuh
ODBC Driver 17 for SQL Server terinstal di OS, bukan hanya paket Python-nya.
Diuji pada Python 3.12.9, great_expectations 1.19.1, pandas 3.0.5, pyodbc 5.3.0.
