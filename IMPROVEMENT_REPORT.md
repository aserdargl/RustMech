# RustMech iyileştirme raporu

Tarih: 15 Kasım 2026. Kapsam: proje kökü, `tests/`, `tools/`, paketleme tanımları
ve README. Önce `AGENTS.md` ve `README.md` okundu; `studio_style.py` ile
`assets/ui/datenburg.qss` incelendi.

**Sonuç:** README güncellendi; dört mekanik OpenFOAM birim testi ve yerel SSH
testi için çevrimdışı atlama seçeneği eklendi. Uygulama kodu, arayüz ve sayısal
çekirdekler değiştirilmedi. Python süreci erişim engeli nedeniyle başlatılamadı;
testlerin geçtiği veya başarısız olduğu belirlenemedi. Geçmiş günlüklerdeki
başarılar bu oturumda yeniden doğrulanmış sayılmadı.

## 1. Bulunan eksikler

### Tamamlanmamış adaptörler ve planlar

| İş | Kaynak ve durum | Bu turdaki karar |
| --- | --- | --- |
| Mekanik OpenFOAM | `openfoam_backend.py::write_openfoam_case_skeleton`, `NotImplementedError`; `OPENFOAM_CAPABILITIES.implemented=False` | Bilinçli pasif adaptör. Etkinleştirilmedi; koruyucu testler eklendi. |
| FFT Homogenization, MPM, Peridynamics | `nonlinear_compression.py::_add_planned_method_pages`, yalnız açıklama sayfaları | Çalışan yöntem değiller; README'ye eklendi. |
| Geometriye kırpılmış voxel mesh | README: “ertelendi; şimdilik uygulanmayacak” | Kullanıcı talimatı gereği uygulanmadı. |
| Genel TRUBA mekanik yolunda çekme/eğilme | `remote_operations.required_capability`, `astm_e8_subsize` ve `iso178_preferred` reddediliyor | Ayrı Kratos profil kodu olması, genel uzak adaptörün tamamlandığını göstermiyor. |
| TRUBA MAPDL | `remote_operations.py`, `remote_worker.py` | Uzak lisans/çalışma ortamı adaptörü nitelendirilmemiş, bilinçli olarak kapalı. |
| Dağıtık OpenFOAM ve seçili kare indirme | README: genel uzak yol tek süreçli, hazırlanmış kareler birlikte indiriliyor | Çok düğümlü MPI ve isteğe bağlı zaman adımı indirme tamamlanmamış işler. |
| CFD/CHT fizik ve mesh sınırları | `flow_analysis.py`, `thermal_analysis.py`, README | Basamaklı Kartezyen sınırlar; CHT'de sabit özellikli tek fazlı laminer su. Yüzeye oturan sınır tabakası, kaynama vb. basit hata düzeltmesi değil. |

Mekanik OpenFOAM taslağı, gerçek `simpleFoam` ve `chtMultiRegionFoam` yollarından
ayrıdır. Taslağı etkinleştirmek veya boş vaka yazıcısıyla tamamlanmış göstermek
güvenli bir düzeltme olmazdı.

### Test kapsamı boşlukları

- Başlangıçta 43, eklemeden sonra 44 `test_*.py` dosyası var. Son statik sayım
  293 `def test_...` tanımı buldu. Bu sayı çalıştırılan test sayısı değildir;
  alt testleri, atlamaları ve yükleme hatalarını ölçmez.
- Geometri, Rust eşdeğerliği, mesh önbelleği, sonuç kimlikleri, malzeme birimleri,
  Enter davranışı, TRUBA sahte taşıma/oturumları, iptal, CFD/CHT dosya sözleşmeleri
  ve pencere tercihleri için testler mevcut. Testlerin tümü eksik sayılmamalı.
- OpenFOAM'ın doğrudan fabrika/çözüm/dosya yan etkisi sözleşmeleri için ayrı test
  dosyası yoktu; dört test eklendi. Önceden bazı UI/mesh-convergence testleri
  pasif OpenFOAM seçimini dolaylı denetliyordu.
- `test_window_state.py` geometri, splitter, büyütülmüş durumdan normal boyuta
  dönüş ve ekran dışı ikincil pencereyi kapsıyor. Ayrı süreçte yeniden başlatma,
  küçültülmüş durum, gerçek çoklu monitör/DPI değişimi ve `QMainWindow` dock
  düzeni için ek kontroller gerekli. Kalıcılık bu tur yeniden sınanmadı.
- `test_real_local_ssh_password_handshake` TRUBA'ya/internete gitmiyor fakat
  gerçek loopback soketi açıyor. Bunu çevrimdışı koşuda atlamak için
  `RUSTMECH_OFFLINE=1` eklendi. Diğer sahte SSH testleri korunuyor.
- Suite yalnız saf birim testlerinden oluşmuyor: bazı testler gerçek çocuk/işçi
  süreçleri, yerel Netgen/Rust DLL'leri veya Git Bash kullanıyor. Saf birim,
  Qt, yerel entegrasyon ve canlı ağ testleri ayrı gruplara ayrılmalı.
- Gerçek ANSYS lisanslı çözümü, TRUBA/SLURM, WSL/OpenFOAM ve EXE/VTK etkileşim
  kontrolleri ayrı araçlarda. Unittest başarısı bunların veya sayısal/deneysel
  doğruluğun yerine geçmez. Mesh/zaman adımı bağımsızlığı ayrıca gerekir.
- Coverage aracı çalıştırılmadı; satır/dal kapsam yüzdesi belirlenmedi.

### Mimari, paketleme ve dokümantasyon

- `nonlinear_compression.py` yaklaşık 153 kB; diyalog, iş akışı ve sonuç
  koordinasyonu geniş bir modülde. `doe_studio.py`, `remote_controller.py` ve
  `mesh_convergence.py` de büyük. Davranış testleri korunarak aşamalı ayrıştırma
  uygun; büyük yeniden düzenleme bu turun güvenli kapsamına alınmadı.
- `Run-RustMech.ps1`, `Build-Exe.ps1` ve `Build-NetgenBridge.ps1` Python seçimini
  tekrarlıyor. `Build-Exe.ps1 -Python` ile başlatıcı farklı ortamlar kullanabilir;
  yorumlayıcı ve bağımlılık sürümleri kaydedilmeli.
- Projede `.venv` yok. Başlatıcı ve PyCharm komşu
  `D:\MySoftware\Gitterburg\.venv\Scripts\python.exe` ortamını kullanıyor.
  `pyvenv.cfg` ve IDE Python 3.14 gösteriyor, fakat süreç başlatılamadı.
  Bütüncül temel/test bağımlılık bildirimi veya kilit dosyası yok; MAPDL, TET ve
  TRUBA gereksinimleri ayrı dosyalarda.
- `Build-Exe.ps1` Rust çekirdeği ve standart Netgen paketlerini topluyor.
  Özel Netgen çekirdeği/array bridge için eşdeğer açık toplama adımları görünmüyor;
  paketin kaynakla aynı hızlı yolu kullanması ayrıca sınanmalı.
  `tools/finalize_distribution.py` için doğrudan test bulunmadı.
- `get_solver_capabilities` bilinmeyen kimlikte MAPDL yeteneklerine düşerken
  `create_solver_backend` hata veriyor. Eski ayar uyumluluğu ve hata sözleşmesi
  testlerle netleştirilmeli; burada değiştirilmedi.
- README güncel kullanım ile tarihsel hızlandırma turlarını karıştırıyor:
  bazı bölümlerde EXE yeniden paketlendiği, bazılarında paketlenmediği/ABI 2
  kaldığı yazıyor. Başa tarihsel kayıt ayrımı ve güncel test açıklaması eklendi.
  Geçmiş ölçümler silinmedi; yeniden üretilmiş gibi sunulmadı.

### Kullanılmayan/tekrarlayan dosyalar ve depo hijyeni

- Kökte 65 Python dosyası, 68 `.log` (15'i `build_*.log`) ve 25 JSON var.
  Benchmark kaynakları/çıktıları kökte dağınık; `build/`, `benchmarks/`,
  `results/` ve birden fazla dağıtım klasörü de bulunuyor.
- Kök Python dosyaları ile `tests/` ve `tools/` Python dosyalarının SHA-256
  karşılaştırmasında birebir aynı dosya grubu bulunmadı. Bu sonuç mantıksal kod
  tekrarının olmadığı veya bütün dosyaların kullanıldığı anlamına gelmez.
- `calculix_pilot.py` bağımsız bir giriş destesi üretme pilotu; solver registry'de
  CalculiX adaptörü yok. Benchmark ve `tools/verify_*` dosyaları da bağımsız
  komutlar olabilir. Uygulama importunun bulunmaması silme gerekçesi değil;
  kesin olarak kullanılmadığı ispatlanan bir dosya ilan edilmedi.
- Dağıtımlarda dört dosya karşılaştırıldı: `solver_api.py`, `remote_operations.py`,
  `openfoam_backend.py`, `nonlinear_compression.py`. `dist/RustMech/_internal/remote_sources`
  içindeki dördü kaynakla aynı. `dist-truba-final` içinde sonuncu;
  `dist-truba-ready` ve `dist-truba-verified` içinde `solver_api.py` ile
  `nonlinear_compression.py` farklı. Bu 16 dosyalık inceleme bütün dağıtımın veya
  DLL'lerin eşdeğerliğini kanıtlamaz. Diğer `dist*` klasörlerinde aynı konumda
  karşılaştırılacak bu dosyalar bulunmadı.
- `.git` dizini yok. `.gitignore` var ancak `dist-*`, sonuçlar, kök günlükleri,
  ölçüm çıktıları ve kullanıcı çalışma verileri için kapsam/saklama politikası
  net değil. Hiçbir dosya silinmedi veya taşınmadı.

## 2. Yapılan değişikliklerin dosya listesi

| Dosya | Değişiklik |
| --- | --- |
| `README.md` | Güncel destek/plan durumu, tarihsel EXE/ABI notlarının kapsamı, başlatıcıyla aynı Python seçimi, çevrimdışı unittest komutu ve entegrasyon sınırları. |
| `tests/test_openfoam_backend.py` | Dört test: pasif yetenekler, fabrika, çözüm girişiminin/çıktı klasörünün oluşmaması ve dışa aktarım reddinde mevcut dosyanın korunması. |
| `tests/test_truba_connection.py` | Yalnız gerçek yerel SSH soketi testine `RUSTMECH_OFFLINE=1` atlama dekoratörü. Değişken yokken mevcut test davranışı korunur. |
| `IMPROVEMENT_REPORT.md` | Bu rapor. |

Denetim kanıtları `build/improvement-audit-20261115/` altında:

- `unittest-baseline.log`: ilk test komutunun ham PowerShell hata çıktısı.
- `unittest-offline.log`: çevrimdışı tam test komutunun ham hata çıktısı.
- `unittest-openfoam.log`: yeni test dosyasının çalıştırma denemesinin ham hata çıktısı.
- `inventory.json`: statik sayımlar ve birebir Python tekrar kontrolü.
- `distribution-source-comparison.json`: seçili dağıtım kaynak karşılaştırmaları.
- `readme-powershell-syntax.json`: README PowerShell bloğunun sözdizimi kontrolü.
- `ide-checks.json`: IDE inceleme sonuçları ve çalıştırma/derleme retleri.

`build/` zaten `.gitignore` içinde; uzun süreli kanıt arşivi için ayrı saklama
politikası gerekir. Uygulama kaynaklarına, stile, `native/` ve
`_backup_before_codex_20261115` içeriğine yazılmadı.

## 3. Çalıştırılan komutlar ve sonuçlar

Komutlar proje kökünden denendi. Python yolu başlatıcı ve PyCharm
`get_python_environment` yanıtından tespit edildi.

| Komut / araç | Gözlenen sonuç |
| --- | --- |
| `& '..\Gitterburg\.venv\Scripts\python.exe' -c "import sys; print(sys.executable); print(sys.version)"` | Yorumlayıcı oluşturulamadı: erişim engellendi. |
| `& 'C:\Users\Ahmet Serdar\AppData\Local\Programs\Python\Python314\python.exe' -c "import sys; print(sys.executable); print(sys.version)"` | Mevcut temel Python da erişim engeli verdi. |
| `py -0p` | Python 3.14 yolunu listeledi; test çalıştırması değil. PATH üzerinde `python` komutu bulunmadı. |
| `& '..\Gitterburg\.venv\Scripts\python.exe' -m unittest discover -s tests -v` | Süreç başlamadı; `unittest-baseline.log`. |
| `$env:RUSTMECH_OFFLINE='1'; & '..\Gitterburg\.venv\Scripts\python.exe' -m unittest discover -s tests -v` | Süreç başlamadı; `unittest-offline.log`. Önceki ortam değişkeni finally ile geri yüklendi. |
| Aynı çevrimdışı ortamda `& '..\Gitterburg\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_openfoam_backend.py' -v` | Süreç başlamadı; `unittest-openfoam.log`. |
| PyCharm `execute_run_configuration`, `tests/test_remote_operations.py:14` | Otomatik onay denetimi çalıştırmayı reddetti. |
| PyCharm `get_file_problems`, değişen iki test dosyası | Yeni OpenFOAM dosyasında sorun yok; SSH dosyasında 2 WARNING ve 1 WEAK WARNING, ERROR yok. |
| PyCharm `build_project`, değişen iki test dosyası | Otomatik onay denetimi derlemeyi reddetti; derleme yapılmış sayılmaz. |
| PowerShell `Parser.ParseInput` | README'nin ilk PowerShell bloğu: 0 sözdizimi hatası. Blok çalıştırılmadı. |
| PowerShell dosya sayımı / `Get-FileHash -Algorithm SHA256` | Envanter ve seçili 16 dağıtım dosyası karşılaştırması tamamlandı. |

Ham unittest çıktısından ilgili bölüm (günlükteki kodlama olduğu gibi):

```text
python.exe : Unable to create process using '"D:\MySoftware\Gitterburg\.venv\Scripts\python.exe" -m unittest discover -
s tests -v': Eri?im engellendi.
    + FullyQualifiedErrorId : NativeCommandError

PowerShell invocation success: False
```

Yeni dosya komutunda aynı hata `-s tests -p test_openfoam_backend.py -v`
argümanlarıyla alındı. Dış PowerShell aracının çıkış kodunun 0 olması testlerin
geçtiğini göstermez; Python komutunun hemen ardından alınan `$?` değeri False.
Python/unittest başlamadığı için test sürecine ait güvenilir çıkış kodu veya
`Ran ... tests / OK / FAILED / skipped` özeti yok.

**Çalıştırılmış test: 0. Geçen/başarısız/atlanan test sayıları: belirlenemedi.**
Yeni dört testin ve çevrimdışı dekoratörün çalışma zamanı davranışı doğrulanmadı.
Ağ kullanan test veya canlı doğrulama aracı çalıştırılmadı.

SSH dosyasındaki IDE uyarıları mevcut `LocalClient.connect` imzası, `connect`
dönüş değeri ve `paramiko.AUTH_SUCCESSFUL` referansıyla ilgili. Yeni dekoratörün
dışındaki satırlarda; test yürütülmeden gerçek arıza varsayılmadı. IDE incelemesi
Python derlemesi veya test çalıştırması değildir.

Çalıştırma ve derleme ret mesajı:

```text
MCP tool call requires approval, but approval policy is never
```

Oturum politikası onaya izin vermediğinden yetki yükseltme istenmedi; kısıtlamayı
aşacak bir çalıştırma yolu denenmedi.

## 4. Önceliklendirilmiş iyileştirme önerileri

| Öncelik | Alan | Somut sonraki iş / kabul ölçütü |
| --- | --- | --- |
| P0 | Test ortamı | Python süreç başlatma erişimini uygun ortamda düzelt; README çevrimdışı komutunu çalıştır, ham çıktı ve gerçek çıkış kodunu sakla. Gerçek uygulama arızaları ancak sonra belirlenebilir. |
| P0 | Sürüm kontrolü | Git deposundan önce kullanıcı verileri, kimlik bilgileri, yedekler, sonuçlar ve büyük ikililer için izleme politikasını belirle. Kaynak/test/build tanımlarını ilk commit'e al. Uzak depoya gönderim ayrıca yetkilendirilmelidir. |
| P1 | Test ayrımı | Saf birim, Qt, yerel native/süreç ve canlı entegrasyon grupları oluştur. Ağ kapalı CI aşamasında skip nedenlerini görünür kıl. Yeni OpenFOAM testleri dahil suite'i çalıştır. |
| P1 | Tekrarlanabilir ortam | Projeye ait `.venv` ve temel/test/çözücü bağımlılık grupları oluştur; Python/VTK/PySide6/Netgen sürümlerini kilitle. Komşu projeye bağımlılığı kaldır. |
| P1 | Paketleme | Tek yayımlanan dağıtım konumu, kaynak kimliği, bağımlılık sürümleri ve DLL hash/ABI manifesti üret. Temiz makinede açılış/HEX/TET smoke kontrolü ekle. Özel Netgen hızlı yolu veya fallback durumunu raporla. |
| P1 | UI kalıcılığı | Dock, küçültülmüş pencere, ayrı süreçte açılış ve monitör/DPI testlerini izole QSettings ile genişlet. Enter ve açık TRUBA düğmesi davranışını koru; görünümü değiştirme. |
| P2 | Mimari | Diyalog koordinasyonunu saf ayar/istek üretimi ve iş yürütmeden ayır. Python seçimini merkezileştir; bilinmeyen çözücü davranışını tanımla. Her adımda regresyon testleri çalıştır. |
| P2 | Sayısal test | Küçük sabit ağlarda kuvvet dengesi, işaret/birim ve mesh/zaman adımı bağımsızlığı referanslarını sürümle. Gerçek çözücü ve mock sonuçlarını ayrı raporla. |
| P2 | Performans | Soğuk/sıcak mesh, aktarım, VTK çizimi, çözüm ve tepe belleğini ayrı ölç. Benchmark'a donanım, bağımlılık, girdi/hash, tekrar sayısı ve medyan ekle. Büyük CHT sonuçları için seçili kare indirmeyi değerlendir. |
| P2 | Dokümantasyon | Güncel kurulum/destek matrisini README'de; tarihsel hızlandırma ve EXE notlarını sürümlü değişiklik/benchmark belgelerinde tut. Doğrulamaları belirli kaynak/dağıtım kimliğine bağla. |
| P2 | Depo hijyeni | Yeni günlükleri `build/logs/<tarih-is>/`, ölçümleri `benchmarks/results/<tarih-is>/` altında üret. Eski dosyaları taşımadan önce referanslarını çıkar; dağıtımlar için saklama politikası oluştur. Bu tur taşıma/silme yapılmadı. |
| P3 | Ek yöntemler | Mekanik OpenFOAM, genel uzak çekme/eğilme ve MPI için ayrı gereksinim/kabul testleri tanımla. Fiziksel doğrulama olmadan etkinleştirme. Ertelenen voxel planı bu aktif önerinin dışındadır. |

## 5. Yapılmayanlar ve nedenleri

- Başarısız test düzeltmesi yapılmadı: testler başlamadı; gerçek test arızası
  gözlenmedi. Ortam erişim hatası uygulama hatası olarak raporlanmadı.
- Rust/Netgen derlemesi, cargo test, benchmark, EXE paketleme ve gerçek çözücü
  çalıştırmaları yapılmadı. Mevcut DLL/dağıtımlar değiştirilmedi.
- TRUBA, ANSYS lisansı, WSL/OpenFOAM veya harici ağ kullanılmadı. Kimlik bilgileri
  okunmadı/raporlanmadı. Gerçek SSH loopback testi de çalışmadı.
- Kırpılmış voxel mesh uygulanmadı: README ve kullanıcı açıkça ertelemiş.
- Arayüz görünümü, Enter davranışı, TRUBA panel açılışı ve pencere kalıcılığı
  kodu değişmedi. Görsel/kalıcılık testi yapılmış gibi sunulmadı.
- `native/` upstream kodu ve `_backup_before_codex_20261115` içeriği değiştirilmedi;
  bu klasörler tarama kapsamı dışında tutuldu. Dosya silinmedi.
- Kullanılmayan dosya temizliği, dağıtım birleştirme, bağımlılık kurulumu ve Git
  başlatma yapılmadı. Kullanım/saklama politikası olmadan güvenli küçük düzeltme
  sayılmadı; somut öneriler yukarıda verildi.

**Doğrulama sınırı:** tamamlanan kontroller dosya/örüntü envanteri, seçili SHA-256
karşılaştırmaları, README PowerShell sözdizimi ve IDE statik incelemesidir.
Test yürütme, çözücü doğruluğu, paket çalışması ve pencere tercihlerinin yeniden
başlatmada korunması bu oturumda doğrulanmadı.
