# RustMech SOLID değerlendirmesi ve iyileştirme planı

19 Kasım 2026 · Kaynak incelemesi · Temel commit `6c40f84`

İlk adım, mekanik analiz ve sonuç üretimini masaüstü arayüzünden ayırmak. Yerel çocuk süreç ve TRUBA işçisi bugün ortak analiz veya raporlama koduna erişmek için `nonlinear_compression.py` modülünü yüklüyor. Bu modül aynı zamanda ana Qt penceresini ve önizleme koordinasyonunu tanımlıyor. Bu bağımlılığı kaldırmak, mevcut sayısal davranış korunurken hem yerel hem uzak çalışma yollarını daha kolay sınanabilir hâle getirir. [Yerel süreç](analysis_process.py) · [TRUBA işçisi](remote_worker.py) · [Masaüstü modülü](nonlinear_compression.py)

## SOLID değerlendirmesi

| İlke | Durum | Kod kanıtı ve etkisi |
| --- | --- | --- |
| **S — Tek sorumluluk** | Yüksek öncelik | `RustMechDialog` ayarları okuyor, önizlemeyi zamanlıyor, işi başlatıyor ve sonucu gösteriyor. Aynı modüldeki `run_analysis` doğrulama, çözüm ve dosya üretimini yürütüyor. `DoEStudioWidget` ön kontrol, yürütme, tablo ve ilerleme kaydını birleştiriyor. Bir iş akışı değişikliğinin etkisi birden fazla çalışma alanına yayılabiliyor. [Ana diyalog](nonlinear_compression.py) · [DoE](doe_studio.py) |
| **O — Açık/kapalı** | Kısmen uygulanmış | `SolverBackend` yeni adaptör için bir sınır sağlıyor. Çözücü kimlikleri modelde, yetenekler `SOLVER_REGISTRY` içinde ve oluşturma koşulları fabrikada tanımlı. TRUBA’da yetenek eşlemesi ortak, yürütme seçimi ayrı bir koşul zincirinde. Eklenen bir kimlik veya işlem bu tanımlardan birinde eksik kalabilir. [Model](model.py) · [Çözücü API’si](solver_api.py) · [İşlem kaydı](remote_operations.py) · [İşçi](remote_worker.py) |
| **L — Yerine geçebilirlik** | Sözleşme riski | Bilinmeyen çözücü kimliği için yetenek sorgusu MAPDL döndürüyor ve fabrika `ValueError` veriyor. Testler iki davranışı da açıkça koruyor. Ortak sonuç türü kısmi sonuç ile hatayı birlikte taşıyabiliyor. MAPDL ve Kratos okuyucularının eşdeğerliğini değerlendirmek için indeks, düğüm eşlemesi, birim ve tamamlanma sözleşmesi ortak testlerle sınanmalı. [Mevcut kimlik testleri](tests/test_solver_api_contract.py) · [Sonuç sözleşmesi](solver_api.py) · [MAPDL](mapdl_backend.py) · [Kratos](kratos_backend.py) |
| **I — Arayüz ayrımı** | Sınırlı sorun | `SolverBackend` iki üyeli, dar bir protokol. `SolutionReader` eğri için adım/reaksiyon verisini ve alan dışa aktarımı için düğüm başına gerilme/deplasmanı tek protokolde sunuyor. `extract_curve` alan verisi okumuyor. İlk ayrım, tüketicilerin gerek duyduğu üyeleri tiplemek olabilir. [Protokoller](solver_api.py) · [Sonuç tüketicileri](nonlinear_compression.py) |
| **D — Bağımlılıkların tersine çevrilmesi** | Yüksek öncelik | `run_analysis` bir `SolverBackend` alıyor, MAPDL kurucusu başlatıcıyı ve okuyucuyu dışarıdan kabul ediyor. Buna karşılık hesap süreçleri Qt modülüne bağımlı ve `RemoteController.auth()` doğrudan `window.truba_dialog` üzerinden çalışıyor. `AnalysisQueueWidget` ortak çalışma döngüsünü DoE ekranını devralıp örnekleme kontrollerini gizleyerek kullanıyor. [Yerel süreç](analysis_process.py) · [Uzak süreç](remote_worker.py) · [TRUBA denetleyicisi](remote_controller.py) · [Analiz kuyruğu](analysis_queue.py) |

`analysis_project.py` ile `project_workspace.py` arasındaki dosya modeli ve arayüz ayrımı, aynı yapının mekanik analiz için yerel örneği. Rust ve Netgen tarafında kapsam, Python köprüleri ve ABI sınırıyla sınırlı. Sayısal çekirdeklerin algoritmik yapısı bu incelemede değerlendirilmedi. [Proje modeli](analysis_project.py) · [Proje çalışma alanı](project_workspace.py) · [Rust köprüsü](rust_mesh.py) · [Netgen köprüsü](netgen_kernel.py)

## Uygulama sırası

### 0. Başlangıç davranışını kayda al

- Yerel tekil analiz, mekanik TRUBA işlemi ve bir toplu iş için mevcut sonuç sözleşmesini kaydet. Ağ yeniden kullanımı, `summary.json` anahtarları, tamamlanma durumları, kısmi sonuç dosyaları, iptal ve yeniden bağlanma kimlikleri karşılaştırma kapsamına girsin.
- **Kabul ölçütü.** İlgili odak testlerinin koşum ve toplama durumu belli olsun. Bir refaktörü doğrulamayı engelleyen mevcut başarısızlıklar, o refaktörden önce yeniden üretilebilir hâle getirilsin. [Ağ yeniden kullanımı](tests/test_analysis_mesh_reuse.py) · [Çocuk süreç](tests/test_analysis_process.py) · [TRUBA yürütme](tests/test_truba_dispatch.py) · [DoE yürütme](tests/test_doe_run_control.py)

### 1. Mekanik analiz ve raporlamayı Qt’den ayır

- İlk değişiklikte `extract_curve`, `export_graphs`, `export_field_results` ve doğrudan yardımcılarını Qt bağımlılığı bulunmayan bir sonuç modülüne çıkar. Sonraki değişiklikte `RunPaths`, tekil çalışma, toplu analiz ve Kratos sonuç kurtarma iş akışını arayüzden bağımsız bir modüle al. Yerel süreç, TRUBA işçisi ve standart profil çalışma yolunu yeni girişlere bağla. Eski içe aktarma yollarını kullanan araçlar için geçici yeniden dışa aktarma kullan.
- **Kabul ölçütü.** Yerel çocuk süreç ve mekanik TRUBA işi, `nonlinear_compression` veya `PySide6` yüklemeden çalışabilsin. Aynı girdide ağ yeniden kullanımı, JSON/dosya sözleşmesi ve kısmi sonuç davranışı korunsun. PyInstaller paketi ile TRUBA kaynak paketinde taşınan Python modülleri doğrulansın. [Masaüstü paketi](Build-Exe.ps1) · [TRUBA kaynak paketi](truba_remote.py)

### 2. Çözücü kayıt ve sonuç sözleşmesini netleştir

- Çözücü tanımını yetenekler ve gecikmeli fabrika çağrısıyla ortak bir kayda bağla. Çalıştırma yolunda geçersiz kimliği açıkça reddet. Eski kayıtlardaki varsayılana dönme davranışını hangi dosya biçimlerinin kullandığını doğrula ve gereken uyumluluğu yükleme sınırında koru. Pasif mekanik OpenFOAM seçeneğinin destek durumu korunmalı.
- MAPDL ve Kratos için ortak okuyucu testleri ekle. Adım sayısı, düğüm sırası, sonuç birimleri, eksik veri ve hata ile birlikte gelen kısmi sonuçları kapsa. Eğri tüketicisi için dar bir protokolü bu sözleşme üzerinde tanımla. Alan dışa aktarımı gerekli adım ve düğüm verilerini kullanmayı sürdürsün.
- **Kabul ölçütü.** Kayıt ve fabrika aynı kimlik kümesini kullanır. Geçersiz kimlik çözüm başlatmaz, eski dosya senaryoları açık testlerle kapsanır ve her etkin adaptör ortak sonuç testlerini geçer. Kratos devam ve mevcut mekanik OpenFOAM testleri de geçer. [Çözücü sözleşmesi](tests/test_solver_api_contract.py) · [Kratos](tests/test_kratos_backend.py) · [OpenFOAM](tests/test_openfoam_backend.py)

### 3. DoE ve analiz kuyruğunun çalışma döngüsünü ortaklaştır

- Ortak yürütme kodunu önce durum geçişleri, satır seçimi ve sonuç uygulamadan başlayarak arayüzden çıkar. İki ekran aynı yürütme bileşenini kullansın. Kuyruk JSON’u ve klasör sonuçlarının yazımını mevcut saklama sınırlarında tut. Dışa aktarma hatası, sonraki satıra geçiş ve kullanıcı iptali için mevcut davranışı örneklerle doğrula.
- **Kabul ölçütü.** Çalıştır, durdur, zaman aşımı ve ilerleme geçişleri Qt penceresi oluşturmadan sınanabilir. Kuyruk şeması, atomik yazım, eşzamanlı düzenleme koruması, yarıda kalan işin yeniden açılması ve numuneyi klasör ekranına geri alma akışı korunur. Masaüstü değişikliği sırasında kompakt kontroller, Enter davranışı ve pencere/bölücü kalıcılığı doğrulanır. [DoE](tests/test_doe_run_control.py) · [Sonuç kalıcılığı](tests/test_doe_results_persistence.py) · [Klasör işleri](tests/test_folder_batch.py) · [Klavye](tests/test_dialog_keyboard.py) · [Pencere](tests/test_window_state.py)

### 4. TRUBA işlem eşlemesini ve pencere bağımlılıklarını sadeleştir

- Uzak işlem tanımlarını doğrulama ve işçi işleyicisiyle eşleştir. Göndericide ve işçide aynı işlem kimliği ile yetenek doğrulansın. Var olan Slurm gönderimi, takip ve sonuç alma katmanlarını kullan. `RemoteController` içindeki kimlik doğrulama ve oturum yaşam döngüsü bağımlılıklarını küçük adımlarla açık girdilere dönüştür.
- **Kabul ölçütü.** Her kayıtlı işlem geçerli bir işleyiciye ve yeteneğe bağlanır. Desteksiz işlem Slurm’a gönderilmez. Mekanik, DoE, Flow ve Thermal çağrılarının yetenekleri ile paket biçimi korunur. Sahte taşıma testleri iptal, oturum kapanışı, yeniden bağlanma ve sonuç indirmeyi kapsar. [İşlemler](tests/test_remote_operations.py) · [Uzak iş yaşam döngüsü](tests/test_truba_remote.py) · [İptal](tests/test_truba_cancel.py) · [Kapanış](tests/test_truba_close.py)

Her aşamayı bağımsız geri alınabilir değişiklikler olarak tamamla. İlk uygulama paketi, başlangıç davranışının kayda alınması ve mekanik sonuç üretiminin Qt modülünden çıkarılmasıdır. Çözücü semantiği, kayıt şemaları, sayısal algoritmalar ve native ABI ancak bunları değiştirmeyi gerektiren ayrı bir ihtiyaç varsa ele alınmalı.
