# DoE: plan oluştur → Run → hepsini sırayla analiz et — iyileştirme planı

Tarih: 16 Kasım 2026. Hedef akış: kullanıcı deney planını kurar, **Run** der, program
tüm deneyleri sırayla çözer ve sonuçları güvenle saklar.

## 1. Mevcut durum (çalışan kısım)

- Plan: tasarımlar + faktör aralıkları (grid/LHS) veya açık örnek kombinasyonları
  (`doe_sample_*`); en çok 5000 satır; satır başına tam ayar görüntüsü (`design_settings`).
- Çalıştırma: `start_batch_solve` → tek satırlık sıralı kuyruk (`_launch_next_in_queue`,
  `_poll_worker`); her satır ayrı süreçte (`AnalysisProcess`), Stop = zorla durdurma.
  Tamamlanmış satırlar atlanır. Yerel (ANSYS) ve TRUBA (Kratos) yolu var.
- Kalıcılık: plan dosyası (`doe_plan.py`, atomik, sürümlü, sonuçları da tutar) ve
  `analysis_queue.py` (kilitli, atomik, özet kontrollü, yarım kalan işi "Interrupted" yapar).
- Her çalışma klasöründe `settings.json` var (tekrarlanabilirlik iyi).

## 2. Bulgular

Kanıt düzeyi: **[ölçüldü]** çalıştırılarak doğrulandı, **[kod]** kodu okuyarak
(çalıştırılmadı), **[log]** mevcut günlüklerden.

### P0 — veri kaybı / devam edememe

| # | Bulgu | Kanıt |
|---|-------|-------|
| F1 | CSV'yi içe aktarmak sonuçları siler: tamamlanmış satır "Pending" olur, kuvvet/enerji/çalışma klasörü kaybolur; sonraki Run hepsini yeniden çözer. | [ölçüldü] dışa-içe aktarma gidiş-dönüşü: `Completed, 123 N, X:/run1` → `Pending, None, ''`. Kod: `import_csv_path` yalnızca girdi sütunlarını okur. |
| F2 | Ana veri seti sabit dosyaya yazılıyor (`ROOT/doe_master_dataset.csv`), her çözümden sonra **baştan** (`"w"`, atomik değil) ve yalnızca tablodaki güncel satırlarla. İkinci bir plan ilkinin sonuçlarını ezer; yazma sırasında çökme dosyayı bozar. | [kod] `doe_studio.py` `_poll_worker`/`export_csv_path`. Dosyadaki 2 satırın ikisi de kuyruk işi kimliği (`JOB-…`) taşıyor; DoE değil, başka bir çalışmanın kalıntısı olduğuyla tutarlı. |
| F3 | Veri seti yazılamazsa hata sessizce yutuluyor (`except OSError: pass`); kullanıcı sonuç kaybını fark etmez. | [kod] |
| F4 | Plan kendiliğinden kaydedilmiyor (yalnızca elle "Save plan"). Uygulama kapanırsa/çökerse bellekteki sonuçlar gider. Plan yüklenince "Solving…" gibi bayat durumlar olduğu gibi kalır (`QueueStore`'daki "Interrupted" mantığı DoE'de yok). | [kod] `save_plan_path` yalnızca kaydet iletişim kutusundan çağrılıyor. |

### P1 — dayanıklılık ve denetim

| # | Bulgu | Kanıt |
|---|-------|-------|
| F5 | Satır başına zaman aşımı yok; takılan bir MAPDL/çözüm kuyruğu süresiz tutar. | [kod] `analysis_process.py` yalnızca iç `join` zaman aşımları içeriyor. |
| F6 | Hata politikası yok. `Failed`, `Invalid`, `Mesh Disconnected`, `partial_solution` satırları her Run'da yeniden denenir (tek tamamlanma ölçütü: metin "completed" ile başlar). Kısmi çözüm gerçekten oluşuyor. Durum alanı serbest metin. | [kod]; [log] `RustMech.log`: `Analysis status: partial_solution`. |
| F7 | Tutarsız hata davranışı: satır çözümü hata verirse kuyruk devam eder, ama **başlatma** hatasında (`Failed to start`) kalan tüm kuyruk silinir. | [kod] `_launch_next_in_queue` `except` bloğu `self._queue.clear()`. |
| F8 | Run başında toplu ön kontrol yok (ayar/çözücü/lisans/disk doğrulaması); hata, ilgili satıra gelince ortaya çıkar. Anlık görüntüsü olmayan satırlar (CSV'den gelenler) ve TRUBA yürütme ayarları **her satırda** canlı arayüzden okunur (`_settings_provider()`); çalışma sırasında arayüzde yapılan değişiklik sonraki satırları etkiler. | [kod] |
| F9 | İlerleme yalnızca "n/N" sayacı; satır süresi, tahmini bitiş, günlük bağlantısı CSV'de yok. | [kod] |

### P2 — verimlilik

| # | Bulgu | Kanıt |
|---|-------|-------|
| F10 | Her satırda MAPDL yeniden başlatılıp kapatılıyor (lisans + açılış yükü). | [log] her çalışmada `Starting ANSYS MAPDL...`; [kod] `mapdl_backend.py` `launch_mapdl`/`exit`. Süre **ölçülmedi**. |
| F11 | TRUBA'da bir satır = bir iş, sıralı beklenir; küme paralelliğinden yararlanılmaz. | [kod] `run_remote_doe`. |
| F12 | Ön kontrol (`_evaluate_row_mesh`) ağı üretip yalnızca özet döndürür; Run (`_solve_row`) ağı yeniden üretir. | [kod] |
| F13 | Kütle iki yolda farklı hesaplanıyor (`size**3·fraction` ↔ `grid.volume`); standart numune profillerinde tutarsız olabilir. | [kod] |

### P3 — kullanım ve analiz

| # | Bulgu | Kanıt |
|---|-------|-------|
| F14 | Sonuçlar yalnızca tabloda/CSV'de; yerleşik grafik/yanıt yüzeyi/karşılaştırma yok. | [kod] |
| F15 | `ROOT` paketli uygulamada exe klasörü; `HumanResults/` ve veri seti oraya yazılır. Program klasörü yazılamazsa (ör. Program Files) kayıt başarısız olur. | [kod] `model.py` `ROOT` |
| F16 | Kuyruk yaşam döngüsü testi eksik: başarısız/kısmi satır, kapanma anı, devam etme, CSV gidiş-dönüşü. Mevcut testler dolaylı (`test_design_pool`, `test_forced_stop_ui`, `test_remote_mode_ui`). | [kod] |
| F17 | İki paralel sistem (DoE kuyruğu ve `AnalysisQueueWidget`) benzer yaşam döngüsünü taşıyor; güçlü kalıcılık yalnızca ikincisinde. | [kod] |

## 3. Plan

### Faz 1 — Veri güvenliği (önce yapılmalı)
1. **Sonuç günlüğü:** her satır bittiğinde `<plan adı>.results.json` (atomik: geçici dosya + `replace`, `QueueStore`'daki kilit/özet mantığı yeniden kullanılır).
2. **CSV içe aktarma sonuçları geri yükler** (durum, yoğunluk, kütle, eleman, kuvvet, EA, SEA, E*, ν*, von Mises, çalışma klasörü). Kabul: gidiş-dönüş testi, tamamlanan satır Run'da atlanır.
3. **Plan başına veri seti:** sabit `doe_master_dataset.csv` yerine `<plan>_<zaman>.csv`; ayrıca ekleme-yalnızca ortak veri seti (plan kimliği sütunuyla) istenirse ayrı seçenek.
4. **Yazma hatası görünür:** durum çubuğunda/uyarıda göster, kuyruğu duraklat.
5. **Kendiliğinden plan kaydı** her satırdan sonra; yükleme sırasında "Solving/Checking/TRUBA" → "Interrupted" (ve devre dışı), `QueueStore` ile aynı kural.

### Faz 2 — Çalıştırma denetimi
1. **Durumlar numaralandırılır:** Pending, Running, Completed, Partial, Failed, Invalid, Disconnected, Cancelled, Interrupted. Metin yalnızca gösterim; tamamlanma ve yeniden deneme ölçütü bu alanlardan.
2. **Run ön kontrolü:** tüm satırlar için `validate_settings`, çözücü/lisans erişimi, tahmini disk, uyarı özeti; kullanıcı onayıyla başla.
3. **Ayar görüntüsü Run başında dondurulur** (canlı arayüzden sonradan okunmaz).
4. **Hata politikası:** "devam / N hatada dur / satır başına k yeniden deneme"; başlatma hatası da aynı politikaya bağlanır (F7). "Yalnız başarısızları çalıştır", "Kısmi satırları yeniden dene".
5. **Satır başına zaman aşımı** (ayarlanabilir), aşımda zorla durdur ve "Failed (timeout)".
6. **Ölçümler:** satır süresi, başlangıç/bitiş, günlük yolu CSV'ye; kalan süre tahmini.

### Faz 3 — Verimlilik
1. MAPDL oturumunu kuyruk boyunca yeniden kullan (ölçüm sonrası karar: önce açılış süresini ölç).
2. Ön kontrolde üretilen ağı ayar özetiyle önbellekle ve çözümde yeniden kullan.
3. TRUBA: iş dizisi/eşzamanlı gönderim (üst sınırlı), sonuçları sıra beklemeden topla.
4. Kütle hesabını tek fonksiyona indir.

### Faz 4 — Analiz ve kullanım
1. Sonuç paneli: yoğunluk–SEA, yoğunluk–E* vb. dağılım grafiği, en iyi/Pareto satırı işaretleme.
2. Kullanıcı başına veri klasörü (`%LOCALAPPDATA%`) seçeneği; `HumanResults/` konumu ayarlanabilir.
3. DoE ve analiz kuyruğunun ortak "çalıştırma günlüğü" katmanına birleştirilmesi.

### Her faz için ortak kurallar
- Yeni arayüz öğeleri `AGENTS.md` kurallarına uyar: kompakt, içeriğe göre boyutlu; Enter hiçbir
  şeyi (Run dahil) tetiklemez; pencere boyutu/yerleşimi kalıcıdır.
- Her madde için önce başarısız test, sonra düzeltme (`tests/test_doe_*`); gerçek ANSYS/TRUBA
  dışı, sahte çözücüyle koşar.

## 4. Doğrulama sınırı

Yapılan: kodun okunması, F1'in çalıştırılarak doğrulanması, günlük incelemesi.
**Yapılmayan:** gerçek bir çok satırlı ANSYS koşusu, TRUBA işi, satır başına MAPDL açılış
süresinin ölçümü, disk kullanımı ölçümü. İncelenen DoE/örnek modülleri henüz git'e
eklenmemiş çalışma kopyasıdır; bulgular bu hâle göre geçerlidir.

## 5. Faz 1 durumu (uygulandi)

Tamamlandi: sonuc CSV'sinin geri okunmasi (tamamlanan satirlar atlanir), atomik CSV yazimi, hata raporlama, her satir sonrasi otomatik kayit ve kurtarma plani, yarim kalan satirlarin Interrupted olarak isaretlenmesi. 8 yeni test: tests/test_doe_results_persistence.py.

Bilinen acik sorun (Faz 1'den bagimsiz, dogrulandi): tests.test_doe_sample_settings icindeki splitter testi ([313, 188] != [317, 190]) Faz 1 oncesi kodda da ayni sekilde basarisiz.