# Mekanik yöntemlerin uygulama planı

TPMS basma, gevrek kırılma ve diğer çökme yöntemleri için [TPMS kırılma ve çökme yol haritası](assets/help/tpms_fracture_roadmap.md) güncel kapsamı ve doğrulama adımlarını içerir.

Mekanik çalışma alanında Finite Element, MAPDL ve Kratos üzerinden çalışır. FFT Homogenization için periyodik TPMS ve kafes hücrelerini çözen DAMASK bağlantısı vardır. Peridynamics, gevrek TPMS basma için kullanılabilir. MPM çalışma alanı, 3B TPMS plaka basmasını yerel Kratos MPM veya TRUBA üzerinden çalıştırır. [MPM kullanımı ve çıktı biçimi](README.md#mpm-ile-tpms-basma)

## Teslim sırası

| Sıra | İlk çalışır teslim | Kabul ölçütü |
| --- | --- | --- |
| 1. FFT Homogenization | Tam periyodik bir TPMS veya lattice hücresini düzenli ızgarada örnekle. Doğrusal elastik katı için altı bağımsız ortalama şekil değiştirme durumu çöz. Arayüzden etkin 6×6 rijitlik matrisini, MPa birimini, bileşen sırasını, katı hacim oranını ve her çözümün yakınsama bilgisini göster ve dışa aktar. Boşluk düzenlileştirmesi kullanılıyorsa değerini kaydet. | Homojen hücre analitik izotropik matrisi yeniden üretir. İki fazlı katmanlı bir referans analitik değerleri karşılar. Bir TPMS örneğinde üç ızgara düzeyi ve boşluk rijitliği duyarlılığı raporlanır. Yakınsamayan koşu hata olarak kaydedilir. |
| 2. Material Point Method (MPM) | Kullanılabilir. 3B TPMS numunesi iki sürtünmesiz rijit plaka arasında açık zaman adımlı ve deplasman kontrollü olarak basılır. İki plaka için kuvvet–yer değiştirme eğrisi ve son adımdaki 3B parçacık alanları kaydedilir. | Windows ve Linux işçi koşuları doğrulandı. Analitik veya FE referansıyla reaksiyon karşılaştırması, ızgara ve zaman adımı duyarlılığı sonraki sayısal doğrulamalardır. |
| 3. Peridynamics | Önce elastik deformasyonu, sonra çentikli bir çekme numunesinde çatlak başlangıcı ve ilerlemesini doğrula. İlk sürümde tercih edilecek modelin elastik sabit ve kırılma enerjisi aralığını açıkça tanımla. Parçacık aralığı, etkileşim ufku, yükleme, hasar alanı ve kuvvet–yer değiştirme eğrisini kaydet. | Hasarsız çubuk analitik çekme tepkisini karşılar. Çentikli referansta çatlak başlangıç yükü ve yolu tekrarlanır. Ufuk ve parçacık aralığı inceltmesiyle sonuç duyarlılığı gösterilir. Referans veri ve kabul toleransları prototipten önce seçilir. |

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
3. **MPM ve Peridynamics kapsamını referanslarla genişlet.** MPM için darbe, sürtünmeli temas ve plastisite, Peridynamics için daha genel kırılma modelleri sonraki kapsamdır. Yeni modellerin doğruluğunu, çalışma süresini ve sonuç aktarımını referans vakalarda ölç.
4. **Her teslimde uçtan uca kontrol yap.** Örneği aç, yöntem ayarlarını değiştir, analizi açık komutla başlat, iptal ve geçersiz girdi davranışını dene, sonucu görüntüle ve projeyi yeniden aç. Referans girdileri, çözücü sürümü, hata ölçüleri ve ağ/zaman adımı duyarlılığı proje ile tekrarlanabilir olmalı. Arayüzde yöntemi çalışır olarak göstermek için ilgili sayısal referans ve masaüstü akışı birlikte geçmeli.
