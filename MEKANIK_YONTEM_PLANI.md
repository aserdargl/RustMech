# Mekanik yöntemlerin uygulama planı

19 Kasım 2026. Mekanik çalışma alanında Finite Element, MAPDL ve Kratos üzerinden çalışır. FFT Homogenization için periyodik TPMS ve kafes hücrelerini çözen yerel DAMASK bağlantısı eklendi. Kullanım ve çıktı biçimi [README](README.md#damask-ile-fft-homojenizasyonu) içinde açıklanıyor. Kırpılmış Voxel ağı, Material Point Method ve Peridynamics sıradaki teslimlerdir. TRUBA ve DoE desteği, her yöntemin iş ve sonuç biçimi yerelde doğrulandıktan sonra eklenebilir.

## Teslim sırası

| Sıra | İlk çalışır teslim | Kabul ölçütü |
| --- | --- | --- |
| 1. FFT Homogenization | Tam periyodik bir TPMS veya lattice hücresini düzenli ızgarada örnekle. Doğrusal elastik katı için altı bağımsız ortalama şekil değiştirme durumu çöz. Arayüzden etkin 6×6 rijitlik matrisini, MPa birimini, bileşen sırasını, katı hacim oranını ve her çözümün yakınsama bilgisini göster ve dışa aktar. Boşluk düzenlileştirmesi kullanılıyorsa değerini kaydet. | Homojen hücre analitik izotropik matrisi yeniden üretir. İki fazlı katmanlı bir referans analitik değerleri karşılar. Bir TPMS örneğinde üç ızgara düzeyi ve boşluk rijitliği duyarlılığı raporlanır. Yakınsamayan koşu hata olarak kaydedilir. |
| 2. Kırpılmış Voxel ağı | Bir düzlem ve eğri yüzeyle kesilen küçük vakalarda, mevcut basamaklı HEX ağını referans alarak kesilmiş hücreleri oluştur. Küçük hücre yönetimini, ortak yüzleri ve çözücü eleman biçimini prototipte doğrula. Başarılı biçimi Finite Element altında ek bir ağ seçeneği olarak bağla ve menüdeki Voxel sayfasından aynı iş akışına erişim ver. | Kırpılmış hacim analitik veya yüzeye oturan TET referansına yakınsar. İç boşluk, çakışan hücre, geçersiz eleman ya da kopuk yüz oluşmaz. Hem ağ hem sınır etiketleri önizleme ile aynı kaynaktan gelir. Desteklenen çözücüde içe aktarma ve küçük doğrusal elastik çözüm geçer. |
| 3. Material Point Method (MPM) | Küçük bir 3B katı numunede açık zaman adımlı, elastik ve sürtünmesiz deplasman kontrollü plaka basması. Parçacık yoğunluğu, arka plan ızgarası ve yükleme süresini ayardan al. Parçacık alanlarını, plaka tepki kuvveti–yer değiştirme eğrisini ve enerji geçmişini göster. | Rijit ötelemede kütle ve momentum korunur. Küçük şekil değiştirmede reaksiyon analitik veya FE referansıyla uyuşur. Büyük şekil değiştirme örneğinde temas ve enerji davranışı, ızgara ve zaman adımı inceltmesiyle raporlanır. |
| 4. Peridynamics | Önce elastik deformasyonu, sonra çentikli bir çekme numunesinde çatlak başlangıcı ve ilerlemesini doğrula. İlk sürümde tercih edilecek modelin elastik sabit ve kırılma enerjisi aralığını açıkça tanımla. Parçacık aralığı, etkileşim ufku, yükleme, hasar alanı ve kuvvet–yer değiştirme eğrisini kaydet. | Hasarsız çubuk analitik çekme tepkisini karşılar. Çentikli referansta çatlak başlangıç yükü ve yolu tekrarlanır. Ufuk ve parçacık aralığı inceltmesiyle sonuç duyarlılığı gösterilir. Referans veri ve kabul toleransları prototipten önce seçilir. |

## Uygulama adımları

### DAMASK FFT doğrulaması

DAMASK 3.1.0 ile 8³ homojen katı ve iki fazlı katmanlı referans hücrelerde,
analitik rijitlik matrisinden en büyük bileşen sapması 0,00001 MPa altında
kaldı. 3 mm periyotlu, hedef katı oranı %30 olan Gyroid sheet için E = 1000 MPa,
ν = 0,3 ve boşluk/katı rijitlik oranı 0,01 kullanıldı.

| Izgara | Örneklenen katı oranı | C₁₁ (MPa) | C₄₄ (MPa) |
| --- | ---: | ---: | ---: |
| 16³ | %30,47 | 125,64 | 37,97 |
| 24³ | %29,40 | 146,84 | 46,46 |
| 32³ | %29,98 | 155,43 | 47,78 |

24³ ile 32³ arasında C₁₁ yaklaşık %6 arttı. 24³ ızgarada boşluk/katı oranı
0,001'e indirildiğinde C₁₁ 112,15 MPa ve C₄₄ 35,35 MPa oldu. Her iki
oranda da altı yükleme yönü yakınsadı. Bu hücre için nihai tasarım değerleri
daha ince ızgara ve daha düşük boşluk rijitliğiyle doğrulanmayı gerektirir.

### Teslim adımları

1. **FFT ile başla.** `structure_library.py` ve `periodic_sampling.py` içindeki yapı alanını hücre merkezlerinde, yinelenen uç düzlemi olmadan örnekle. Isı veya akış çözücüsünü çağırmadan homojen hücre ve katmanlı referansları önce Qt dışında çalıştır. İlk arayüzde periyodikliği tanımlı TPMS ve lattice ailelerini destekle. Rastgele porous ve içe aktarılan geometriler için periyodiklik doğrulaması ayrı bir teslimdir.
2. **İlk çözümü masaüstüne bağla.** `analysis_process.py` üzerinden ilerleme, iptal ve hata davranışını kullan. Her yöntem için girdi anlık görüntüsünü, yöntem ve sürüm kimliğini, birimleri, yakınsama durumunu ve sonuç dosyalarını kaydet. `analysis_project.py` ile eski projeleri açma testini ekle. Mekanik çalıştırma düğmesini seçili yöntemin yeteneğine bağla. Plan sayfasında Finite Element çözümünün yanlışlıkla başlatılmasını ve iptal edilmiş koşunun başarılı olarak listelenmesini önle.
3. **Voxel kararını gerçek çözücüyle ver.** `mesh_engine.py` ve mevcut yüzey/ağ kalite kontrollerini kullan. `mapdl_backend.py` ve `kratos_export.py` için desteklenen hücre tiplerini ayrı ayrı doğrula. İlk prototipte kullanılabilir cut-cell aktarımı çıkmazsa yüzeye oturtulmuş HEX ve yerel tetra bölme adaylarını hacim hatası, eleman kalitesi ve aktarım üzerinden karşılaştır. Geçen yöntem için çözücü desteğini ayrı ayrı aç.
4. **MPM ve Peridynamics için çözücü seçimini referanslarla yap.** Adayların lisansını, Windows ve TRUBA kurulabilirliğini, desteklenen malzeme/temas modellerini, iptal davranışını ve sonuç biçimlerini karşılaştır. Bir aday seçmeden önce küçük vakada bellek ve çalışma süresini ölç. MPM için darbe, sürtünmeli temas ve plastisite, Peridynamics için daha genel kırılma modelleri ilk teslimden sonra gelir.
5. **Her teslimde uçtan uca kontrol yap.** Örneği aç, yöntem ayarlarını değiştir, analizi açık komutla başlat, iptal ve geçersiz girdi davranışını dene, sonucu görüntüle ve projeyi yeniden aç. Referans girdileri, çözücü sürümü, hata ölçüleri ve ağ/zaman adımı duyarlılığı proje ile tekrarlanabilir olmalı. Arayüzde yöntemi çalışır olarak göstermek için ilgili sayısal referans ve masaüstü akışı birlikte geçmeli.

### Kırpılmış Voxel TET4 prototipi

`trimmed_voxel_mesh.py`, kübik bir Kartezyen ızgaranın her hücresini ortak
yüzlerde aynı köşegenleri kullanarak altı tetrahedraya böler. Referans alanı
ızgara köşelerinde örnekler, sıfır geçişlerini kenarlar üzerinde doğrusal
olarak hesaplar ve kırpılan hücreleri TET4 olarak döndürür. Izgaranın
örneklemediği ince özellikler kaybolabilir. Çok küçük pozitif hacimli sınır
elemanları ağda kalır.

Depo kökünde RustMech'in Python ortamıyla çalıştırın.

```powershell
python benchmark_trimmed_voxels.py --output build/trimmed-voxel-benchmark.json
```

Varsayılan ızgaralar 8³, 16³ ve 32³'tür. HEX8, analitik alanı hücre
merkezlerinde örnekleyip RustMech'in mevcut seçim ve ağ oluşturma işlevlerini
kullanır. Netgen, sabit üçgen referans yüzeyini alır ve global ve yerel hedef
eleman boyutu olarak ızgara aralığını kullanır. Hacim referansı analitiktir.
Yüzey hatası, düğümlerde, kenar ortalarında ve üçgen merkezlerinde örneklenen
iki yönlü en büyük mesafedir. Küre için referans örnekleri analitik yüzeye
izdüşürülür. Süre üç ağ üretiminin medyanıdır. Bellek sütunu, üretilen VTK
ağının tahmini depolama boyutunu gösterir.

| Geometri | Izgara | HEX8 hacim % / yüzey mm | Kırpılmış TET4 hacim % / yüzey mm | Netgen TET4 hacim % / yüzey mm |
| --- | ---: | ---: | ---: | ---: |
| Eğik düzlem | 8³ | 0,13267 / 0,084127 | ≈0 / ≈0 | ≈0 / ≈0 |
| Eğik düzlem | 16³ | 0,00553 / 0,042500 | ≈0 / ≈0 | ≈0 / ≈0 |
| Eğik düzlem | 32³ | 0,02856 / 0,021032 | ≈0 / ≈0 | ≈0 / ≈0 |
| Küre | 8³ | 1,93753 / 0,101952 | 6,04842 / 0,016368 | 6,21553 / 0,015621 |
| Küre | 16³ | 0,68586 / 0,052265 | 1,50493 / 0,004029 | 1,73794 / 0,004138 |
| Küre | 32³ | 0,14175 / 0,026632 | 0,37705 / 0,001011 | 0,66749 / 0,001551 |

Kırpılmış TET4 kürede daha küçük yüzey hatası sağladı, ancak 32³'te hacim
hatası HEX8'den yüksek kaldı. 32³ kürede kırpılmış ağ 58.499, Netgen ağı
26.543 eleman üretti. Bu çalıştırmada medyan üretim süresi sırasıyla
0,249 ve 1,420 saniye, VTK ağının tahmini boyutu 3.180 ve 1.416 KiB oldu.
En düşük ölçeklenmiş Jacobian sırasıyla yaklaşık 0,0000018 ve 0,26 oldu.
On sekiz ağın tamamı yüzler üzerinden tek bir bağlantılı bileşen
oluşturdu ve dış yüzeyde açık veya manifold olmayan kenar saptanmadı.
Çözücüye geçmeden önce düşük kaliteli sınır elemanlarının yönetimi ve ince
Gyroid duvarlarının korunması doğrulanacak.

### Sınır elemanı kalitesi ve ince Gyroid duvarları

Kırpma öncesinde ızgara düğümleri, komşu kenardaki en yakın uygun sıfır
geçişine taşınır. Hareket ızgara aralığının en çok %18'i kadardır ve küp
yüzlerindeki düğümler kendi yüzlerinde kalır. Adayda arka plan tetrahedralleri
ters dönmemeli, en düşük ve 1. yüzdelik Jacobian kötüleşmemeli ve en az
biri iyileşmelidir. Açık kenar, yüz-bağlantılı bileşen sayısı veya yüzeyin
Euler karakteristiği değiştiğinde daha küçük kaydırma denenir. Uygun aday
bulunamazsa başlangıç ağı kullanılır.

```powershell
python benchmark_trimmed_voxels.py --quality-study --output build/trimmed-voxel-quality.json
```

| Geometri | Izgara | En düşük ölçeklenmiş Jacobian, önce → sonra | En büyük yüzey sapması (mm), önce → sonra |
| --- | ---: | ---: | ---: |
| Eğik düzlem | 8³ | 0,000184 → 0,0508 | ≈0 → ≈0 |
| Eğik düzlem | 16³ | 0,000170 → 0,0508 | ≈0 → ≈0 |
| Eğik düzlem | 32³ | 0,000170 → 0,0508 | ≈0 → ≈0 |
| Küre | 8³ | 0,000160 → 0,00776 | 0,016368 → 0,018560 |
| Küre | 16³ | 0,000116 → 0,00907 | 0,004029 → 0,004663 |
| Küre | 32³ | 0,00000183 → 0,00877 | 0,001011 → 0,001184 |
| İnce Gyroid | 8³ | 0,01575 → 0,03472 | 0,067049 → 0,066596 |
| İnce Gyroid | 16³ | 0,000632 → 0,000632 | 0,046677 → 0,046677 |
| İnce Gyroid | 32³ | 0,000469 → 0,00891 | 0,002007 → 0,002128 |

Gyroid testi, bir adet 1 mm'lik periyotta `|Gyroid| ≤ 0,45` duvarını
kullanır. Yüzey karşılaştırması için 96³ referans ızgarası kullanıldı. Kalınlığı
0,125 mm veya daha az olan 192 eksen doğrultulu kesitte, iki çeyrek nokta
ve orta noktanın ağ içinde kaldığı kontrol edildi. 8³'te her iki ağ
128/192 kesiti korudu ve 12 yüz-bağlantılı bileşen oluşturdu. 16³ ve 32³'te
her iki ağ 192/192 kesiti korudu ve üç bileşen oluşturdu. 16³'te kaydırma
adayları topoloji kontrolünden geçmediği için başlangıç ağı kullanıldı.
32³'te 1. yüzdelik Jacobian kürede 0,00194'ten 0,0544'e, Gyroid'de
0,00332'den 0,0366'ya çıktı.
