# Klasörden sıralı mekanik analiz

1. Ana **Mechanic** çalışma alanında malzeme, ağ, yükleme, temas ve çözücü
   parametrelerini ayarlayın.
2. **Designs** yanındaki **Batch Analysis** düğmesine tıklayın ve klasörü seçin.
   STL, OBJ, PLY ve VTP dosyaları alt klasörleriyle birlikte, dosya adına göre
   doğal sırada taranır (`part2`, `part10` öncesindedir). `Result` klasörleri ve
   bağlantılı klasörler taranmaz. Koordinatlar mm kabul edilir.
3. Basma yönünü seçin: X, Y, Z; ilk numunede seçilen destek/yükleme
   yüzeylerinden yön alma; veya her numunede elle seçim.
4. **Check folder** ile ön kontrolü çalıştırın. Malzeme ve analiz ayarlarının
   kopyası bu anda alınır. Otomatik mod, her numunenin kendi uç sınırlarında
   düz ve paralel yüzeyler arar. Aynı düzlemdeki birden fazla temas yaması
   birlikte seçilir. Yön bilgisi aktarılır; üçgen kimlikleri aktarılmaz.
5. **Inspect / select faces...** ile seçili numunenin yüzeylerini inceleyin veya
   düzeltin. Destek yeşil, yükleme turuncudur. Hazır olmayan numuneler
   işaretlenmez. Bunları hazırlayın veya çalışmanın dışında bırakın.
6. Çözücü olarak mevcut çalışma alanını, ANSYS MAPDL, Kratos veya her ikisini
   seçin. Aynı parametreler kullanılır; çözücünün desteklemediği malzeme, temas
   veya eleman seçimi açık hata verir, sessizce değiştirilmez. İçe aktarılan
   numunelerde HEX seçimi TET4'e çevrilir; mevcut TET4/TET10 seçimi korunur.
   Periyodik ağ yerine Netgen kullanılır.
7. **Add ready specimens to queue** seçin. Çözücü uyumluluğu ve analiz ayarları
   doğrulanır, tüm seçili işler tek işlemde kuyruğa kaydedilir. Aynı kaynak
   içeriğine ve ayarlara sahip işler yeniden eklenmez. Kaynak dosya hazırlıktan
   sonra değişmişse yeniden ön kontrol gerekir.
8. **Open analysis queue > Run analysis queue** ile açıkça başlatın. Her
   numunenin seçili çözücüleri tamamlandıktan sonra sıradaki numuneye geçilir.
   Varsayılan hata politikası sonraki işe devam eder. Tamamlanan işler atlanır;
   hatalı işler aynı kuyruktan yeniden denenebilir. Yeniden açılışta kesilmiş
   işler işaretsizdir; çalıştırmadan önce durumlarını kontrol edin.

Sonuçlar seçilen klasörde tutulur:

```text
Numuneler/
  numune_01.stl
  Result/
    toplu_ozet.csv
    numune_01-<kimlik>/Mechanical/ansys_mapdl/JOB-<kimlik>/
      job.json
      Run_<zaman>/
        summary.json
        01_Geometry/
        02_MeshQuality/
        03_Results/
        04_Graphs/
        Raw/
```

`job.json` kaynak özetini, yönü, ayarları, durumu ve sonuç ölçümlerini içerir;
başarısız işler de raporlanır. Çözücünün ürettiği geometri, alan sonuçları,
eğri CSV dosyaları ve ham dosyalar korunur. Her deneme ayrı çalışma klasörü
kullanır. Aynı isimli farklı numuneler birbirinin çıktısını ezmez. TRUBA'da
mevcut çalışma alanının çözücüsü kullanılır ve indirilen sonuçlar ayrıca bu
klasöre kopyalanır; kopya başarısızsa özgün indirme korunur ve hata gösterilir.

Küp şekli veya izotropi varsayımı otomatik seçim şartı değildir. Kullanıcı
fiziksel basma yönünü belirler. Eğik kaydedilmiş numunelerde ilk numuneden yön
alma veya elle seçim kullanılmalıdır. Düz temas yüzeyi olmayan bir numune için
program yaklaşık yüzey tahmin etmez; geometri düzeltmesi veya farklı bir temas
modeli gerekir. Yük büyüklüğü ölçeklenmez: deplasman mm, kuvvet N olarak aynı
kalır. Pencere boyutu, konumu ve büyütülmüş durumu kullanıcıya özel saklanır.

Bu klasör kuyruğu mekanik basma içindir. Akış ve termal çalışmaların mevcut
ayrı iş akışları korunmuştur.
