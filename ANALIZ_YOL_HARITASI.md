# RustMech çoklu fizik analizleri yapılacaklar matrisi

17 Kasım 2026. Hedef: boru veya kare/dikdörtgen kanal içinde TPMS, lattice ve porous dolguların; üretilen veya içe aktarılan geometrilerle mekanik, akış, bağımsız termal ve birleşik akış–termal analizlerini yapmak. Sonraki aşama sıcaklık ve akış yüklerini mekanik çözüme aktarmaktır.

Ana ihtiyaç mevcut çözücüleri yeniden yazmak değil; ortak geometri, bölge, malzeme ve analiz tanımı üzerinde mevcut mekanik ve OpenFOAM yollarını genişletmektir. Özellikle içe aktarılan geometri ve TPMS dışındaki yapılar bugün CFD/CHT girişinde engellenir. Kare kanal ve bağımsız katı termal analizi için ayrı geliştirme gerekir.

Bu çalışma kaynak kod ve internet araştırmasına dayalı geliştirme planıdır. Mevcut test/README kayıtları yeni çalıştırılmış çözücü doğrulaması olarak değerlendirilmemiştir. Buradaki tamamlanma ölçütleri önerilen kabul koşullarıdır; deneysel doğrulamanın yerini tutmaz.

## Mevcut durum ve kod kanıtı

| Alan | Kaynakta bulunan durum | Hedef açısından sınır | Kanıt |
| --- | --- | --- | --- |
| Mekanik | MAPDL ve TRUBA/Kratos adaptörleri; basma, çekme ve üç nokta eğilme profilleri; doğrusal ve plastik malzeme modelleri | Genel montaj, çok malzemeli bölge ataması ve termal/akış yük aktarımı ortak sözleşmede yok | [solver_api.py](solver_api.py), [standard_tests.py](standard_tests.py), [model.py](model.py) |
| Yapı üretimi | TPMS, Truss Lattice ve Porous aileleri mevcut | Akış/CHT doğrulaması TPMS dışındaki dolgu ailelerini reddediyor | [structure_library.py](structure_library.py), [flow_analysis.py](flow_analysis.py) |
| Akış | Gerçek `simpleFoam`; sürekli, izotermal, sıkıştırılamaz akış; laminer veya k–ω SST | Silindir, +Z doğrultusu, üretilmiş TPMS; basamaklı Kartezyen ağ, sınır tabakası yok | [flow_analysis.py](flow_analysis.py), [flow_workspace.py](flow_workspace.py) |
| Birleşik akış–termal | Gerçek `chtMultiRegionFoam`; zamana bağlı su–katı enerji ve momentum çözümü; arayüz sıcaklık/ısı akısı tanıları | Laminer, sabit özellikler, su için 0–95 °C; yalıtılmış dış sınır; boru duvarı ayrı katı değil; izole su boşlukları reddediliyor | [thermal_analysis.py](thermal_analysis.py), [thermal_workspace.py](thermal_workspace.py) |
| Bağımsız termal | Mevcut Thermal çalışma alanı CHT üzerine kurulu | Akış olmadan katı iletimi, genel ısıl sınırlar ve ısı kaynağı için ayrı çalışma yolu gerekli | [thermal_analysis.py](thermal_analysis.py) |
| Geometri import | STL, OBJ, PLY, VTP; kapalı, manifold, tek bağlı katı; mm kabulü | Mekanik basma için iki karşılıklı paralel düz yüzey seçimine bağlı; CFD/CHT importu engelli; STEP desteği bu okuyucuda yok | [imported_geometry.py](imported_geometry.py), [geometry_import_dialog.py](geometry_import_dialog.py), [flow_analysis.py](flow_analysis.py) |
| Gitterburg aktarımı | `Output > Send to RustMech` ve RustMech `Designs > Design Pool (Gitterburg)`; STL, mm, parametre kopyası, SHA-256 | Mevcut hazırlama ve kuyruk mekanik odaklı; çok bölgeli malzeme/akışkan/topoloji sözleşmesi yok | [design_exchange.py](design_exchange.py), [design_pool.py](design_pool.py), [analysis_queue.py](analysis_queue.py), Gitterburg `ui_components/rustmech_transfer_mixin.py` |
| Fiziksel malzeme import | RustMech JSON malzeme dosyası ve CSV pekleşme eğrisi mevcut | JSON mekanik özelliklere özgü; termal özellikler ayrı giriliyor, ortak katı/akışkan kartı yok | [model.py](model.py), [material_editor.py](material_editor.py), [thermal_analysis.py](thermal_analysis.py) |
| Uzak çalışma ve kayıt | TRUBA oturumu/iş yönetimi, vaka dosyaları, DoE ve mekanik kuyruk mevcut | README genel uzak OpenFOAM akışını tek süreçli olarak tanımlıyor; tüm fiziklere ortak proje/kuyruk ve MPI genişletmesi gerekli | [README.md](README.md), [truba_remote.py](truba_remote.py), [analysis_queue.py](analysis_queue.py) |

Mekanik sekmedeki pasif `openfoam_backend.py` yalnız katı mekaniği için ayrılmış adaptördür. Bu adaptörün pasifliği çalışan ayrı CFD ve CHT yollarının bulunmadığı anlamına gelmez.

`FlowSettings` bugün çapta 8–128 hücre ve 700.000 arka plan hücresi sınırı uygular. Büyük mekanik ağlar için kaldırılan eski limitler CFD limitlerinin de kaldırıldığı anlamına gelmez. `ThermalSettings` ayrıca en çok 200 çıktı aralığı ve istenen adımla hesaplanan 20.000 adım sınırı uygular.

## Öncelik ve iş büyüklüğü

P0: kullanıcının istediği temel iş akışını tamamlamak veya sonuç güvenilirliğini sağlamak için gerekli. P1: kapsamı ve mühendislik kullanımını genişletir. P2: ileri fizik veya optimizasyon.

S: mevcut yola sınırlı ekleme; M: birkaç modülün birlikte değişmesi; L: yeni veri modeli, ağ veya çözücü hattı; XL: çok aşamalı çoklu fizik çalışması. Bunlar göreli büyüklüktür, süre taahhüdü değildir. Satır numaraları geliştirme bağımlılıklarını belirtir.

## Yapılması gerekenler matrisi

| ID | Öncelik | Yapılacak iş | Bugünkü durum | Bağımlılık | Büyüklük | Tamamlanma ölçütü |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | P0 | Ortak analiz projesi: geometri, bileşen/bölge, malzeme, sınır seçimi, fizik, çalışma ve sonuç kimlikleri | Ayrı mekanik/Flow/Thermal ayarları var | — | L | Aynı tasarım ayrı mekanik, CFD, termal ve CHT çalışmalarına atanır; kaydet/aç sonrasında seçim ve birimler korunur; eski kayıtlar taşınır |
| 02 | P0 | Silindir yanında kare/dikdörtgen kanal; bağımsız en, boy, uzunluk, giriş/çıkış uzantısı, numune konumu ve yönelimi | Silindir ve küp numune boyuna bağlı +Z akış | 01 | M | Boş boru ve boş kare kanal çözülür; dolgu yerleştirme/kırpma hacimleri önizleme ile aynıdır |
| 03 | P0 | TPMS dışındaki lattice ve porous yapıları CFD/CHT’ye açmak | Üretim kütüphanesi var, doğrulama engelliyor | 01, 02 | M | Her aileden en az bir yapı iki kanal türünde çalışır; rastgele porous yapı aynı seed ile tekrar üretilir |
| 04 | P0 | İçe aktarılan yüzeyden akışkan hacmi çıkarmak; genel importu basma yüzeyi seçiminden ayırmak | İçe aktarma mekanik hazırlamaya bağlı | 01, 02 | L | STL/OBJ/PLY/VTP dolgu kanala yerleşir; akışkan hacmi kanal eksi katıdan çıkarılır; CFD/CHT için basma yüzeyi istenmez |
| 05 | P0 | Gitterburg tasarım havuzunu CFD ve CHT’ye bağlamak; genel hazırlama ve fizik seçimi | Transfer var, mekanik kuyruğa gidiyor | 01, 04 | M | Gitterburg’dan gelen aynı tasarım mekanik, CFD ve CHT’ye atanır; gönderme veya import analiz başlatmaz |
| 06 | P0 | Ortak katı/akışkan malzeme kartı, JSON import ve CSV özellik tabloları; bölgeye atama | Mekanik JSON/CSV var; termal ayrı | 01 | L | E, ν, plastik eğri, ρ, k, cp, ısıl genleşme ve akışkan μ açık birimlerle yüklenir; eksik zorunlu özellik anlaşılır hata verir; eski mekanik JSON açılır |
| 07 | P0 | Geometri ve bölge ön kontrolü: çakışma, açıklık, minimum duvar/gözenek, geçişli akış, kapalı boşluk, bypass | Bazı manifold/bağlantı kontrolleri var | 02, 03, 04 | M | Hata konumu 3B gösterilir; tasarımdaki ve ağdaki katı oranı/bağlantı sayısı karşılaştırılır; kullanıcı onayı olmadan geometri değiştirilmez |
| 08 | P0 | Yüzeye oturan CFD/CHT ağ hattı; yerel inceltme ve uygun sınır tabakaları | Basamaklı ortak Kartezyen ağ var | 04, 07 | L | TPMS duvarları korunur; `checkMesh` tüm bölgelerde geçer; katman kapsamı ve y+ raporlanır; ince ağda performans çıktıları izlenir [K1] |
| 09 | P0 | Bölge ve yüzey seçimi: inlet/outlet, dış duvar, katı–akışkan, katı–katı, mekanik yük/destek | Sabit patch adları ve mekanik yüzey seçimi | 01, 02, 04 | L | Yeniden ağ üretiminde anlamlı yüzey kimlikleri korunur; hatalı/eksik sınırlar çözümden önce bulunur |
| 10 | P0 | Bağımsız katı termal çözümü: sürekli/geçici iletim, sıcaklık, ısı akısı, hacimsel güç, dış taşınım | Thermal alanı CHT odaklı | 06, 09 | L | Akışkan olmadan katı çözülür; plaka iletimi analitik referansa uyar; geçici enerji depolaması kapanır |
| 11 | P0 | Genel CHT: boru/kutu duvarını ayrı katı bölge yapmak; ısıtılan duvar veya kaynak, dış taşınım, çok malzeme | Su+TPMS var; dış sınır yalıtılmış | 06, 08, 09 | L | Akışkan+dolgu+gövde ayrı bölgelerde çözülür; sınır güçleri, arayüz geçişi ve depolanan enerji birlikte dengelenir [K2] |
| 12 | P0 | CFD/CHT için ağ ve zaman adımı bağımsızlığı; global kütle/enerji dengesi ve doğrulama paketi | Artık, kütle ve CHT arayüz kontrolleri var | 08, 10, 11 | L | En az üç ağ düzeyinde Δp ve ısıl güç değişimi gösterilir; geçici çözümde zaman inceltme yapılır; toleranslar kaydedilir |
| 13 | P0 | Birim, malzeme kaynağı ve analiz varsayımlarını raporlamak; temel performans çıktıları | p/U/T, Δp ve sıcaklık geçmişi var | 06, 10, 11, 12 | M | Debi, Δp, pompalama gücü, ısı gücü ve maksimum sıcaklık aynı raporda; referans alan/uzunluk ve ortalama tanımları görünür |
| 14 | P1 | Gitterburg aktarım paketi v2: bileşenler, bölge kimlikleri, malzeme referansları, dönüşümler, kaynak sürümü | Tek STL+parametre snapshot’u var | 01, 05, 06 | L | Eski paketler açılır; çok malzemeli örnekte atamalar korunur; yalnız renk/etiket fiziksel malzeme kabul edilmez |
| 15 | P1 | STEP/BREP ve çok parçalı montaj importu; birim ve koordinat dönüşümü onayı | Yüzey dosyaları ve tek bağlı katı | 01, 04, 09 | L | mm/m örnekler doğru ölçeklenir; gövde ve dolgu ayrı bölgeler kalır; bozuk katı için onarım tanısı verilir [K3] |
| 16 | P1 | Akış/ısıl sınır seçenekleri: hacimsel/kütlesel debi, basınç farkı, gelişmiş profil, zamana bağlı giriş | Hız girişi ve sabit sıcaklık ağırlıklı | 09, 11 | M | Eşdeğer debi tanımları aynı sonucu verir; geri akış sıcaklığı ve zaman eğrileri açıkça tanımlanır |
| 17 | P1 | Akışkan ve katıda sıcaklığa bağlı özellikler; su yanında hava/yağ vb. için kontrollü kartlar | Sabit özellikli su modeli | 06, 11 | L | μ(T), ρ(T,p), k(T), cp(T) tabloları çözücüye gider; geçerlilik aralığı dışına çıkış bildirilir; özellik sağlayıcısı sürümlenir [K4] |
| 18 | P1 | CHT’de türbülans ve gelişmiş geçici CFD; rejime göre model/y+ seçimi | İzotermal SST var; CHT laminer | 08, 11, 12 | L | Giriş türbülansı kullanıcı tarafından tanımlanır; hedeflenen duvar yaklaşımı ve y+ kontrol edilir; referans vakayla doğrulanır |
| 19 | P1 | Sıcaklık ve basınç alanını mekanik çözüme tek yönlü aktarmak | Ortak mekanik sözleşmede alan yükü yok | 01, 06, 09, 11 | L | T(x,t) katı ağına, basınç/duvar kayması yükleri yüzeye taşınır; kuvvet/moment korunumu ve serbest/kısıtlı ısıl genleşme referansları geçer |
| 20 | P1 | Genel mekanik bölge yükleri ve gövde–dolgu birleşimi; bağlı/temaslı montaj | Numune/deney profilleri var | 01, 06, 09, 14 | L | Basma dışındaki gövde yükleri ve çok malzemeli montaj çözülür; destek kısıtları, reaksiyonlar ve temas tanıları raporlanır |
| 21 | P1 | İki ayrı akışkan devreli TPMS ısı değiştirici ve manifoldlar | Tek su devresi+katı | 08, 09, 11, 14 | XL | İki devre arasında sızıntı/yanlış birleşme yok; her devrede kütle dengesi ve ortak ısıl güç dengesi var [K5] |
| 22 | P1 | Porous ortamın etkin modeli: Darcy–Forchheimer, yönlü geçirgenlik ve termal kapanış | Gözenekleri açıkça çözen mevcut geometri | 03, 06, 12 | L | Çözülmüş hücre/deneyden K ve atalet katsayısı kalibre edilir; açık gözenek modeli ile çalışma aralığında karşılaştırılır [K6] |
| 23 | P1 | Bütün fiziklere ortak analiz kuyruğu, DoE, sonuç karşılaştırma, durdur/yeniden başlat | Mekanik kuyruk ve ayrı CFD/CHT vakaları | 01, 05, 13 | L | Tasarım×malzeme×debi taraması snapshot’larla tekrarlanır; kesilmiş işler kendiliğinden başlamaz; geçersiz işler başarı sayılmaz |
| 24 | P1 | Yerel/TRUBA MPI, kaynak tahmini, checkpoint ve seçili sonuç indirme | TRUBA var; genel uzak OpenFOAM tek süreçli | 08, 11, 23 | L | Aynı vaka seri/paralel tolerans içinde eşleşir; CHT bütün bölgelerde bölünür; iptal/yeniden başlat ve kaynak bütçesi doğrulanır |
| 25 | P1 | Etkin mekanik/ısıl özellikler ve geçirgenlik için periyodik hücre analizi | Yapı ve DoE altyapısı var | 06, 12, 20, 22 | L | E_eff, k_eff ve K yönlere göre çıkarılır; temsilî hacim ve sınır koşulları raporlanır; bunlar katı ana malzemesiyle karıştırılmaz |
| 26 | P2 | Doğal taşınım, radyasyon, temas ısıl direnci; daha sonra faz değişimi | Yerçekimi sıfır, ideal temas, radyasyon kapalı | 11, 12, 17 | L–XL | Her fizik ayrı referansla doğrulanır; basınç ve faz geçerliliği uygulanır; 0–95 °C koruması yalnız model genişledikten sonra değişir |
| 27 | P2 | Çift yönlü akış–yapı ve termal–mekanik etkileşim, hareketli ağ | Tek yönlü aktarım da henüz yok | 19, 20, 24 | XL | Deformasyon akışa geri beslenir; zaman/adım eşleştirme, coupling artıkları ve ağ kalitesi izlenir; preCICE için uyumlu sürümler sabitlenir [K7] |
| 28 | P2 | Çok amaçlı optimizasyon: ısı transferi, pompalama gücü, rijitlik/dayanım, kütle, üretilebilirlik | DoE/ML altyapısı başlangıç sağlar | 12, 13, 19, 23 | XL | Pareto seçenekleri aynı sınır koşullarıyla karşılaştırılır; adaylar gerçek çözücüyle yeniden doğrulanır |
| 29 | P0 | Kaynak sürüm ve dağıtım EXE’sinin yetenek uyumu; temiz ortam kurulum ve küçük referans projeler | README farklı bölümlerde paketleme durumunu farklı tarihlerle anlatıyor | İlk teslim paketi | M | Sürüm/yetenek ekranı gerçek adaptörleri gösterir; temiz kurulumda mekanik, CFD, termal, CHT örnekleri açılır ve çalıştırılır |

## Gitterburg ve malzeme aktarımı

Geometrik numune ile fiziksel malzeme kartı ayrı nesneler olmalıdır. Gitterburg transferi bugün gerçek üretim geometrisini STL olarak ve `collect_project_state()` çıktısını parametreler içinde taşır. Kaynak parametrelerde malzeme etiketi bulunması, RustMech’in E, ν, akma eğrisi, k veya cp değerlerini doğrulanmış bir kart olarak içe aldığı anlamına gelmez.

Önerilen aktarım: Gitterburg’da üret → RustMech’e gönder → tasarım havuzundan seç → kanal/gövde içine yerleştir → bölgeleri ve malzemeleri ata → fizik/sınır koşullarını seç → ağı kontrol et → açık komutla çöz → karşılaştır/raporla.

Paket v2 en az geometri dosyaları, birimler, koordinat dönüşümü, bileşen/bölge kimlikleri, malzeme kartı referansları, kaynak parametreler, sürüm ve içerik hash’leri taşımalıdır. STL tek başına malzeme, CAD hacim topolojisi veya güvenilir sınır adları taşıyan bir değişim sözleşmesi olarak kullanılmamalıdır. Genel yüzey importu ilk adımı sağlar; STEP ve bölgelere ayrılmış yüzeyler daha sonra eklenebilir.

Malzeme kartında katı için E, ν, ρ, plastik veri, k, cp ve ısıl genleşme; akışkan için ρ, μ, k, cp ve gerekirse hal denklemi bulunmalıdır. Veri kaynağı, sıcaklık/basınç geçerlilik aralığı, üretim yöntemi ve ölçüm yönü de saklanmalıdır. Ana malzeme kartına porous yapının etkin E veya k değerlerini atamak, gözenekli geometrinin ayrıca çözüldüğü modelde etkileri iki kez sayabilir.

Mekanikteki g/cm³, MPa, mm ile CFD’deki kg/m³, Pa, m dönüşümleri açık yapılmalıdır. Örneğin 7,85 g/cm³ = 7850 kg/m³; 200.000 MPa = 200 GPa. Sıcaklık farkında °C ve K eşdeğer olsa da mutlak sıcaklık gerektiren çözücü girdileri K olmalıdır. Mevcut mekanik dosyaların birim sözleşmesi korunarak yeni kartlara göç sağlanmalıdır.

## Fizik ve sonuç tanımları

Akış–termal CHT, katı iletimini ve akışkan enerjisini arayüzde birlikte çözer; akışkan sıcaklığının yalnız sonradan katıya verilmesiyle aynı değildir. Mevcut CHT bu temel yeteneği zaten sağlar. Ayrı mekanik çözümde gerilme elde etmek ise 19 numaralı alan aktarımını gerektirir. Deformasyonun akışı değiştirmesi 27 numaralı çift yönlü etkileşimdir.

Porous için iki farklı yaklaşım sunulmalıdır: küçük/orta numunede gerçek gözeneklerin açıkça çözülmesi; büyük numunede kalibre edilmiş etkin direnç ve ısı transferi modeli. Etkin model gözenek içi yerel hız/sıcaklık ayrıntısını aynı çözünürlükte vermez. Momentum için Darcy–Forchheimer eklemek tek başına termal kapanışı sağlamaz; yerel ısıl denge veya iki sıcaklıklı model ayrıca seçilip doğrulanmalıdır [K6].

Karşılaştırma raporunda önerilen tanımlar:

- Hidrolik çap Dh = 4A/P; kanal ve gözenek ölçeğine göre Reynolds sayıları ayrı tanımlanır.
- Pompalama gücü Δp × hacimsel debi; statik/total basınç farkı ve ölçüm düzlemleri açık belirtilir.
- Sabit özellikli akışkanda ısıl güç ṁ cp (T_out − T_in); sıcaklığa bağlı özelliklerde entalpi farkı kullanılır.
- Geçici enerji dengesinde giriş–çıkış entalpi akısı, sınır/kaynak güçleri ve katı+akışkan enerji birikimi birlikte yer alır. Isınan numunede yalnız giriş ve çıkış ısıl güçlerinin eşitliği aranmaz.
- h, Nu ve ısıl direnç hesaplanırken ısı aktarım alanı, duvar/bulk sıcaklığı ve karakteristik boy açık seçilir. Uygun sıcaklık farkı tanımı olmayan bir çalışmada bu değerler zorla üretilmez.
- İki devreli eşanjörde etkinlik/NTU ve toplam ısı geçişi; tek devreli geçici katı ısıtmasından ayrı değerlendirilir.

Deneysel gyroid araştırmaları ısı aktarımı yanında basınç düşümü ve akış direncini de ölçmektedir. Bu nedenle tasarım sıralaması yalnız sıcaklık düşüşüne göre yapılmamalı; eşit debi ve eşit pompalama gücü karşılaştırmaları ayrı sunulmalıdır [K5].

## Önerilen teslim sırası

1. **Ortak temel ve ilk kapsam genişletmesi:** 01, 02, 03, 06, 07, 09. Kare kanal, porous/lattice, ortak malzeme ve proje modeli. Mevcut ağla sonuçlar ön değerlendirme düzeyinde kalır.
2. **Import ve güvenilir ağ:** 04, 05, 08, 12’nin akış kısmı. Gitterburg’dan gelen gerçek dolgu hem boruda hem kare kanalda analiz edilir. Önceki mekanik akış korunur.
3. **Tam temel termal kapsam:** 10, 11, 12, 13, 29. Bağımsız termal, duvar/dolgu/akışkan CHT ve doğrulanmış örnekler. Kullanıcının temel hedefinin tamamlanması için ilk üç teslim birlikte gereklidir.
4. **Mühendislik kapsamı:** 14–25. Malzeme/bölge zengin aktarımı, özellik tabloları, alan yükleri, iki devre, homogenizasyon ve çoklu çalışma. Bu işler birbirinden bağımsız alt teslimlere ayrılabilir.
5. **İleri çoklu fizik:** 26–28. Gereken uygulamaya göre radyasyon/doğal taşınım, FSI ve optimizasyon. İlk hedef için yeni bir mekanik çözücü entegrasyonu zorunlu değildir.

Her teslimde ilgili modül testleri ve küçük analitik/çözücü referansları çalıştırılmalıdır. UI değişirse mevcut kompakt Datenburg kontrol ailesi, Enter davranışı, kullanıcı pencere/splitter/dock tercihleri ve eski kayıtlar korunmalıdır. Ayar düzenleme, import ve panel açma kendiliğinden analiz veya TRUBA bağlantısı başlatmamalıdır.

## Önerilen kabul senaryoları

| Senaryo | Doğrulanan yetenek | Beklenen kontrol |
| --- | --- | --- |
| Boş yuvarlak boru ve kare kanal | Kanal ve CFD | Gelişmiş laminer akış için kesite uygun analitik basınç kaybı/hız profili; giriş uzunluğu etkisinin ayrılması |
| Gyroid dolgu ve aynı kanalın boş referansı | Akış performansı | Aynı debi ve ölçüm düzlemleri; üç ağda Δp yakınsaması; kütle dengesi |
| Seed’i sabit porous ve bir lattice dolgu | TPMS dışı kapsam | Bağlantılı akış yolu, katı hacmi ve tekrarlanabilir geometri; iki kanal kesiti |
| Gitterburg’dan asimetrik tasarım | Aktarım ve import | Ölçek/yönelim/hash doğruluğu; mekanik, CFD, CHT seçimi; mekanik yüzey seçimi CFD’yi engellemez |
| Sabit sıcaklıklı uçlara sahip katı plaka | Bağımsız termal | Analitik sıcaklık profili ve güç; ayrı geçici ısınma kontrolü |
| Isıtılmış kanal duvarı ve dolgu | Genel CHT | Duvar/dolgu/akışkan bölgeleri; arayüz sürekliliği; global enerji ve zaman adımı yakınsaması |
| Serbest ve kısıtlı ısınan çubuk | Termal–mekanik | Serbest genleşmede beklenen uzama; kısıtlı durumda analitik gerilme |
| Basınç altında gövde | Akış–mekanik yük aktarımı | Basınç alanından toplam kuvvet/moment korunumu ve bağımsız mekanik referans |
| Sıcak ve soğuk iki devre | Eşanjör | Devreler arası akış bağlantısı olmaması; iki kütle dengesi; sürekli durumda sıcak/soğuk güç eşleşmesi |

## İnternet araştırması ve teknik dayanaklar

Kaynaklara 17 Kasım 2026’da başvuruldu. Belgelerdeki araç yeteneği RustMech’te uygulanmış özellik olarak işaretlenmemiştir. Çözücü dağıtımı ve sürüm uyumu entegrasyon sırasında ayrıca doğrulanmalıdır.

- **K1 — Yüzeye oturan ağ:** OpenCFD’nin [snappyHexMesh 2606 belgesi](https://doc.openfoam.com/2606/tools/pre-processing/mesh/generation/snappyhexmesh/) yüzey yaklaştırma, snapping ve katman ekleme aşamalarını tanımlar. Bu, mevcut basamaklı ağın yanına üretim amaçlı ayrı bir ağ hattı koymak için adaydır; her ince TPMS’de sorunsuz ağ garantisi değildir.
- **K2 — CHT:** OpenCFD’nin [chtMultiRegionFoam belgesi](https://doc.openfoam.com/2312/tools/processing/solvers/rtm/heat-transfer/chtMultiRegionFoam/) çok bölgeli ısı geçişi için dayanak sağlar. RustMech mevcutta OpenCFD 2606 sözleşmesini kullanır. Foundation’ın [foamMultiRun kaynak kodu](https://github.com/OpenFOAM/OpenFOAM-13/blob/master/applications/solvers/foamMultiRun/foamMultiRun.C) farklı modüler mimariyi gösterir; dağıtımlar yalnız komut adını değiştirerek eşdeğer kabul edilmemelidir.
- **K3 — CAD ve bölge etiketleri:** [Gmsh resmî kılavuzu](https://gmsh.info/doc/texinfo/) CAD/Boolean işlemler, physical groups ve boyut alanları için aday altyapıyı açıklar. Gmsh kullanımı öneri düzeyindedir; mevcut Netgen/Rust mekanik ağ hattını değiştirmek şart değildir.
- **K4 — Akışkan özellikleri:** [CoolProp resmî arayüz belgesi](https://coolprop.org/coolprop/HighLevelAPI.html) sıcaklık/basınçtan termofiziksel özellik sorgularını ve birimlerini açıklar. Su/hava vb. kartlarının hazırlanmasında adaydır; özellik sorgusu tek başına çözücüde sıcaklığa bağlı model entegrasyonu sağlamaz. Katı malzeme veri tabanı yerine kullanılmamalıdır.
- **K5 — Deneysel TPMS eşanjörleri:** [Dutkowski ve diğerleri, Energies 2025](https://www.mdpi.com/1996-1073/18/15/4134), gyroid yüzeyle ayrılan sıcak/soğuk su devrelerinde ısı geçişi ve akış direncini ölçer. İki devre, ısıl güç ve basınç düşümünü birlikte ele alma gereğini destekler; yayımlanan korelasyonlar başka geometri/akış aralıklarına doğrudan taşınmamalıdır.
- **K6 — Etkin porous direnç:** OpenCFD’nin [DarcyForchheimer API belgesi](https://api.openfoam.com/2412/classFoam_1_1porosityModels_1_1DarcyForchheimer.html) hacimsel gözenekli direnç modelini tanımlar. RustMech’te kullanılacak katsayıların numune, yön ve akış rejimine göre kalibrasyonu ayrıca yapılmalıdır.
- **K7 — İleri bağlaşım:** [preCICE OpenFOAM yapılandırması](https://precice.org/docs/adapters/openfoam/config/) ve [CalculiX adaptörü](https://precice.org/docs/adapters/calculix/) ayrık çözücü bağlaşımı için adaydır. CalculiX adaptör belgesinde desteklenen CalculiX sürümü 2.20 olarak belirtilir; güncel CalculiX sürümüyle otomatik uyum varsayılmamalıdır. Mevcut CHT’yi tamamlamak için bu yeni bağımlılık gerekli değildir.

## Bu çalışmanın doğrulama kapsamı

Uygulama kodu ve arayüz değiştirilmedi; bu teslim bir araştırma ve planlama belgesidir. Kaynak kanıtları, bağımlılık numaraları, tablo yapıları ve depo farkı gözden geçirildi. Canlı MAPDL/OpenFOAM/TRUBA analizi çalıştırılmadı. `C:/temp_test/full-suite-last-RustMech.txt` içindeki tarih 2026-11-17 olduğundan günlük tam test paketi tekrar çalıştırılmadı. Bu iş için yeni çalışma zamanı testi gerekli görülmedi.
